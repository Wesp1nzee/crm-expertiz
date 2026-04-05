from __future__ import annotations

import asyncio
import email
import email.message
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr
from typing import TYPE_CHECKING, Any, cast

import aioimaplib
from sqlalchemy import func, select

from src.app.core.config import settings as app_settings
from src.app.core.storage.s3 import s3_storage
from src.app.services.mail.models import (
    MailAttachment,
    MailContent,
    MailFolder,
    MailMessage,
    MailMessageStatus,
    MailMessageType,
    MailSyncState,
)
from src.app.services.mail.schemas import MailSyncResult

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

log = logging.getLogger(__name__)

_IMAP_FOLDER_MAP: dict[MailFolder, str] = {
    MailFolder.INBOX: "INBOX",
    MailFolder.SENT: "Sent",
    MailFolder.DRAFTS: "Drafts",
    MailFolder.SPAM: "Spam",
    MailFolder.TRASH: "Trash",
}

_IMAP_FOLDER_FALLBACKS: dict[MailFolder, list[str]] = {
    MailFolder.SENT: ["Sent", "Sent Items", "SENT"],
    MailFolder.DRAFTS: ["Drafts", "DRAFTS"],
    MailFolder.SPAM: ["Spam", "Junk", "SPAM"],
    MailFolder.TRASH: ["Trash", "Deleted Items", "Deleted", "TRASH"],
}

_POLL_FOLDERS = [
    MailFolder.SENT,
    MailFolder.DRAFTS,
    MailFolder.SPAM,
    MailFolder.TRASH,
]

_IDLE_KEEPALIVE = 28 * 60
_POLL_INTERVAL = 5 * 60
_BACKOFF_BASE = 5
_BACKOFF_MAX = 300


async def _resolve_thread(
    session: AsyncSession,
    parsed: email.message.Message,
    company_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """
    Определяет thread_id и parent_id для нового письма.

    Алгоритм (идентичен Gmail/Outlook):
    1. In-Reply-To → ищем письмо с таким external_message_id.
    2. References (с конца) → ищем любое совпадение.
    3. Не нашли → новый тред.

    Дополнительно: если у найденного родителя thread_id=NULL
    (письмо из старых данных) — назначаем ему thread_id на месте.
    """
    in_reply_to = (parsed.get("In-Reply-To") or "").strip()
    references_raw = (parsed.get("References") or "").strip()

    parent_msg: MailMessage | None = None

    if in_reply_to:
        parent_msg = await session.scalar(
            select(MailMessage).where(
                MailMessage.external_message_id == in_reply_to,
                MailMessage.company_id == company_id,
            )
        )
        if parent_msg:
            log.debug(f"_resolve_thread: matched In-Reply-To={in_reply_to!r} → parent id={parent_msg.id}")

    if parent_msg is None and references_raw:
        for ref_id in reversed(references_raw.split()):
            ref_id = ref_id.strip()
            if not ref_id:
                continue
            candidate = await session.scalar(
                select(MailMessage).where(
                    MailMessage.external_message_id == ref_id,
                    MailMessage.company_id == company_id,
                )
            )
            if candidate:
                parent_msg = candidate
                log.debug(f"_resolve_thread: matched References ref={ref_id!r} → parent id={parent_msg.id}")
                break

    if parent_msg is not None:
        if parent_msg.thread_id is None:
            fixed_tid = uuid.uuid4()
            parent_msg.thread_id = fixed_tid
            await session.flush()
            log.info(f"_resolve_thread: fixed NULL thread_id on parent id={parent_msg.id} → {fixed_tid}")
            return fixed_tid, parent_msg.id

        return parent_msg.thread_id, parent_msg.id

    return uuid.uuid4(), None


class ImapIdleWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        company_id: uuid.UUID,
    ) -> None:
        self._factory = session_factory
        self._company_id = company_id
        self._user_id: uuid.UUID | None = None
        self._client: aioimaplib.IMAP4_SSL | None = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        log.warning("ImapIdleWorker: stop() called")
        self._stop_event.set()

    async def run(self) -> None:
        log.info("ImapIdleWorker [INBOX]: starting...")
        try:
            await self._resolve_user_id()
            log.info(f"ImapIdleWorker [INBOX]: user_id={self._user_id} resolved")
        except Exception as e:
            log.exception(f"ImapIdleWorker [INBOX]: FATAL init error: {type(e).__name__}: {e}")
            return

        if await self._check_needs_initial_sync():
            log.info("ImapIdleWorker [INBOX]: DB is empty, running initial sync (365 days)...")
            try:
                async with self._factory() as session:
                    syncer = ImapSyncer(session, self._company_id, cast(uuid.UUID, self._user_id))
                    result = await syncer.sync_folder(MailFolder.INBOX, days_history=365)
                    log.info(f"ImapIdleWorker [INBOX]: initial sync done: fetched={result.fetched}")
            except Exception as e:
                log.exception(f"ImapIdleWorker [INBOX]: initial sync failed: {e}")

        attempt = 0
        while not self._stop_event.is_set():
            try:
                await self._connect()
                attempt = 0
                await self._idle_loop()
            except asyncio.CancelledError:
                log.info("ImapIdleWorker [INBOX]: cancelled")
                break
            except Exception as exc:
                delay = min(_BACKOFF_BASE * (2**attempt), _BACKOFF_MAX)
                log.exception(f"ImapIdleWorker [INBOX]: error (retry {delay}s): {exc}")
                attempt += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
            finally:
                await self._disconnect()

    async def _check_needs_initial_sync(self) -> bool:
        async with self._factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(MailMessage)
                .where(
                    MailMessage.user_id == self._user_id,
                    MailMessage.company_id == self._company_id,
                    MailMessage.folder == MailFolder.INBOX,
                )
            )
        return (count or 0) == 0

    async def _resolve_user_id(self) -> None:
        from src.app.services.user.models import User

        async with self._factory() as session:
            user = await session.scalar(
                select(User).where(
                    User.email == app_settings.ADMIN_EMAIL,
                )
            )
        if user is None:
            raise RuntimeError(f"No user found for email={app_settings.ADMIN_EMAIL}")
        self._user_id = user.id

    async def _connect(self) -> None:
        log.info(f"ImapIdleWorker [INBOX]: connecting to {app_settings.MAIL_IMAP_HOST}...")
        self._client = aioimaplib.IMAP4_SSL(
            host=app_settings.MAIL_IMAP_HOST,
            port=app_settings.MAIL_IMAP_PORT,
            timeout=30,
        )
        await self._client.wait_hello_from_server()
        await self._client.login(app_settings.MAIL_EMAIL, app_settings.MAIL_PASSWORD)
        log.info(f"ImapIdleWorker [INBOX]: connected as {app_settings.MAIL_EMAIL}")

    async def _disconnect(self) -> None:
        if self._client:
            try:
                await self._client.logout()
            except Exception:
                pass
            self._client = None

    async def _idle_loop(self) -> None:
        assert self._client is not None

        status, sel_resp = await self._client.select("INBOX")
        current_validity = _extract_uidvalidity(sel_resp)
        log.info(f"ImapIdleWorker [INBOX]: selected. Validity: {current_validity}")

        await self._fetch_new_messages(current_validity)

        while not self._stop_event.is_set():
            log.info("ImapIdleWorker [INBOX]: entering IDLE mode...")

            try:
                idle_task = await self._client.idle_start(timeout=30)

                try:
                    msg = await asyncio.wait_for(self._client.wait_server_push(), timeout=600)
                    log.info(f"ImapIdleWorker [INBOX]: push received: {msg}")
                except TimeoutError:
                    log.debug("ImapIdleWorker [INBOX]: 10 min passed, refreshing session...")
                finally:
                    log.debug("ImapIdleWorker [INBOX]: exiting IDLE mode to sync...")
                    self._client.idle_done()
                    await asyncio.wait_for(idle_task, timeout=10)

            except Exception as e:
                log.warning(f"ImapIdleWorker [INBOX]: IDLE failed/interrupted: {e}")
                if "closed" in str(e).lower():
                    raise

            if not self._stop_event.is_set():
                await self._fetch_new_messages(current_validity)
                await asyncio.sleep(2)

    async def _sync_missed_messages(self, current_validity: str | None) -> None:
        await self._fetch_new_messages(current_validity)

    async def _fetch_new_messages(self, current_validity: str | None) -> None:
        assert self._client is not None
        await self._client.noop()

        last_uid = await self._get_last_uid()

        # Используем обычный search с UID критерием
        status, resp = await self._client.search(f"UID {last_uid + 1}:*")

        log.info(f"ImapIdleWorker [INBOX]: DB last_uid={last_uid}, SEARCH status={status}, resp={resp}")

        if status != "OK" or not resp or not resp[0]:
            return

        raw_uids = resp[0].split()
        if not raw_uids:
            return

        # После search с UID-критерием приходят seq numbers — фетчим через uid("FETCH")
        # Поэтому нужно конвертировать: делаем FETCH каждого seq и берём реальный UID из ответа
        new_seq_numbers = []
        for u_bytes in raw_uids:
            try:
                new_seq_numbers.append(int(u_bytes))
            except ValueError:
                continue

        if not new_seq_numbers:
            return

        log.info(f"ImapIdleWorker [INBOX]: PROCESSING {len(new_seq_numbers)} MESSAGES FROM SERVER: {new_seq_numbers}")

        current_max = last_uid

        for seq_int in sorted(new_seq_numbers):
            # Фетчим по seq, внутри метода извлекаем реальный UID из ответа
            real_uid, success = await self._process_single_message_by_seq_and_filter(str(seq_int), last_uid, MailFolder.INBOX)
            if real_uid and real_uid > current_max:
                current_max = real_uid

        if current_max > last_uid:
            await self._update_sync_state(current_max, current_validity)
            log.info(f"ImapIdleWorker [INBOX]: Sync state updated to {current_max}")

    async def _process_single_message_by_seq_and_filter(
        self,
        seq: str,
        last_uid: int,
        folder: MailFolder,
    ) -> tuple[int | None, bool]:
        assert self._client is not None
        try:
            fetch_resp = await self._client.fetch(seq, "(UID FLAGS BODY.PEEK[])")
            if fetch_resp[0] != "OK":
                log.error(f"FETCH FAILED for seq={seq}: status={fetch_resp[0]}")
                return None, False

            real_uid = _extract_uid_from_fetch_response(fetch_resp)
            if real_uid is None:
                log.error(f"Could not extract UID from fetch response for seq={seq}")
                return None, False

            # Вот здесь фильтруем — если UID уже обработан, пропускаем
            if real_uid <= last_uid:
                log.debug(f"ImapIdleWorker [INBOX]: seq={seq} has uid={real_uid} <= last_uid={last_uid}, skipping")
                return real_uid, False

            flags = _extract_flags_from_fetch_response(fetch_resp)
            is_read = r"\Seen" in flags

            raw = _extract_email_body(fetch_resp)
            if not raw:
                log.error(f"EMPTY BODY for seq={seq}, uid={real_uid}. Skipping.")
                return real_uid, True

            parsed = email.message_from_bytes(raw)
            del raw

            async with self._factory() as session:
                stored = await _persist_imap_message(
                    session=session,
                    parsed=parsed,
                    imap_uid=real_uid,
                    folder=folder,
                    company_id=self._company_id,
                    user_id=cast(uuid.UUID, self._user_id),
                    is_read=is_read,
                )
                await session.commit()

            del parsed

            if stored:
                log.info(f"ImapIdleWorker [INBOX]: saved uid={real_uid}")
            else:
                log.warning(f"ImapIdleWorker [INBOX]: uid={real_uid} not stored (duplicate)")

            return real_uid, stored

        except Exception as e:
            log.exception(f"FATAL processing seq={seq}: {e}")
            return None, False

    async def _process_single_message_by_uid(self, uid: str, folder: MailFolder) -> tuple[int | None, bool]:
        assert self._client is not None
        try:
            status, fetch_resp = await self._client.uid("FETCH", uid, "(RFC822 FLAGS)")
            if status != "OK" or not fetch_resp:
                log.error(f"FETCH FAILED for UID {uid}: status={status}")
                return None, False

            real_uid = _extract_uid_from_fetch_response(fetch_resp) or int(uid)
            flags = _extract_flags_from_fetch_response(fetch_resp)
            is_read = r"\Seen" in flags
            raw = _extract_email_body(fetch_resp)

            if not raw:
                log.error(f"EMPTY BODY for UID {uid}. Skipping to avoid infinite loop.")
                return real_uid, True

            parsed = email.message_from_bytes(raw)

            async with self._factory() as session:
                stored = await _persist_imap_message(
                    session=session,
                    parsed=parsed,
                    imap_uid=real_uid,
                    folder=folder,
                    company_id=self._company_id,
                    user_id=cast(uuid.UUID, self._user_id),
                    is_read=is_read,
                )
                await session.commit()

            if not stored:
                log.warning(f"Message UID {uid} was NOT stored (possibly duplicate in _persist_imap_message)")
                return real_uid, True

            return real_uid, True
        except Exception as e:
            log.exception(f"FATAL processing UID {uid}: {e}")
            return None, False

    async def _process_seq_list(self, raw_seq_list: list[bytes], last_uid: int) -> int:
        max_uid = last_uid
        saved = skipped = 0
        for seq_bytes in raw_seq_list:
            if not seq_bytes or seq_bytes == b"":
                continue
            seq_str = seq_bytes.decode()
            real_uid, success = await self._process_single_message_by_seq(seq_str, last_uid, MailFolder.INBOX)
            if real_uid and real_uid > max_uid:
                max_uid = real_uid
            if success:
                saved += 1
            else:
                skipped += 1

        log.info(f"ImapIdleWorker [INBOX]: _process_seq_list: saved={saved}, skipped={skipped}, max_uid={max_uid}")
        return max_uid

    async def _get_last_uid(self) -> int:
        async with self._factory() as session:
            state = await session.scalar(
                select(MailSyncState).where(
                    MailSyncState.user_id == self._user_id,
                    MailSyncState.company_id == self._company_id,
                    MailSyncState.folder == MailFolder.INBOX.value,
                )
            )
        return state.last_synced_uid if state else 0

    async def _update_sync_state(self, uid: int, validity: str | None) -> None:
        async with self._factory() as session:
            state = await session.scalar(
                select(MailSyncState).where(
                    MailSyncState.user_id == self._user_id,
                    MailSyncState.company_id == self._company_id,
                    MailSyncState.folder == MailFolder.INBOX.value,
                )
            )
            if not state:
                state = MailSyncState(
                    user_id=self._user_id,
                    company_id=self._company_id,
                    folder=MailFolder.INBOX.value,
                    last_synced_uid=uid,
                    last_synced_at=datetime.now(UTC),
                )
                session.add(state)
            else:
                state.last_synced_uid = max(state.last_synced_uid, uid)
                state.last_synced_at = datetime.now(UTC)
            await session.commit()

    async def _process_single_message_by_seq(
        self,
        seq: str,
        last_uid: int,
        folder: MailFolder,
    ) -> tuple[int | None, bool]:
        assert self._client is not None
        try:
            fetch_resp = await self._client.fetch(seq, "(UID FLAGS BODY.PEEK[])")
            if fetch_resp[0] != "OK":
                return None, False

            real_uid = _extract_uid_from_fetch_response(fetch_resp)
            if real_uid is None:
                return None, False

            if real_uid <= last_uid:
                return real_uid, False

            flags = _extract_flags_from_fetch_response(fetch_resp)
            is_read = r"\Seen" in flags

            raw = _extract_email_body(fetch_resp)
            if not raw:
                return real_uid, False

            parsed = email.message_from_bytes(raw)
            del raw

            async with self._factory() as session:
                stored = await _persist_imap_message(
                    session=session,
                    parsed=parsed,
                    imap_uid=real_uid,
                    folder=folder,
                    company_id=self._company_id,
                    user_id=cast(uuid.UUID, self._user_id),
                    is_read=is_read,
                )
                await session.commit()

            del parsed
            return real_uid, stored
        except Exception as e:
            log.exception(f"Failed processing seq={seq}: {e}")
            return None, False


class ImapFolderPoller:
    """
    Периодически синхронизирует папки Sent/Drafts/Spam/Trash.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        company_id: uuid.UUID,
        folders: list[MailFolder] | None = None,
        poll_interval: int = _POLL_INTERVAL,
    ) -> None:
        self._factory = session_factory
        self._company_id = company_id
        self._folders = folders or _POLL_FOLDERS
        self._poll_interval = poll_interval
        self._user_id: uuid.UUID | None = None
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        log.warning("ImapFolderPoller: stop() called")
        self._stop_event.set()

    async def run(self) -> None:
        log.info(f"ImapFolderPoller: starting for folders={[f.value for f in self._folders]}")
        try:
            await self._resolve_user_id()
        except Exception as e:
            log.exception(f"ImapFolderPoller: FATAL init error: {e}")
            return

        attempt = 0
        while not self._stop_event.is_set():
            try:
                await self._poll_cycle()
                attempt = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                delay = min(_BACKOFF_BASE * (2**attempt), _BACKOFF_MAX)
                log.exception(f"ImapFolderPoller: error (retry {delay}s): {exc}")
                attempt += 1
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except TimeoutError:
                    pass
                continue

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)
            except TimeoutError:
                pass

    async def _poll_cycle(self) -> None:
        client = aioimaplib.IMAP4_SSL(
            host=app_settings.MAIL_IMAP_HOST,
            port=app_settings.MAIL_IMAP_PORT,
            timeout=60,
        )
        try:
            await client.wait_hello_from_server()
            await client.login(app_settings.MAIL_EMAIL, app_settings.MAIL_PASSWORD)

            for folder in self._folders:
                if self._stop_event.is_set():
                    break
                await self._sync_one_folder(client, folder)
        finally:
            try:
                await client.logout()
            except Exception:
                pass

    async def _select_folder(self, client: aioimaplib.IMAP4_SSL, folder: MailFolder) -> bool:
        candidates = _IMAP_FOLDER_FALLBACKS.get(folder, [_IMAP_FOLDER_MAP[folder]])
        for name in candidates:
            try:
                sel_resp = await client.select(name)
                if sel_resp[0] == "OK":
                    return True
            except Exception:
                pass
        log.warning(f"ImapFolderPoller [{folder.value}]: could not SELECT: {candidates}")
        return False

    async def _sync_one_folder(self, client: aioimaplib.IMAP4_SSL, folder: MailFolder) -> None:
        try:
            if not await self._select_folder(client, folder):
                return

            last_uid = await self._get_last_uid(folder)
            date_from = (datetime.now(UTC) - timedelta(days=30)).strftime("%d-%b-%Y")
            resp = await client.search(f"SINCE {date_from}")

            if resp[0] != "OK" or not resp[1][0]:
                return

            seq_list = [s for s in resp[1][0].split() if s and s != b""]
            if not seq_list:
                return

            log.info(f"ImapFolderPoller [{folder.value}]: {len(seq_list)} messages, last_uid={last_uid}")

            max_uid = last_uid
            for seq_bytes in seq_list:
                if self._stop_event.is_set():
                    break
                seq = seq_bytes.decode()
                try:
                    fetch_resp = await self._fetch_with_retry(client, seq, folder.value)
                    if fetch_resp is None:
                        continue

                    if fetch_resp[0] != "OK":
                        continue

                    real_uid = _extract_uid_from_fetch_response(fetch_resp)
                    if real_uid is None or real_uid <= last_uid:
                        if real_uid:
                            max_uid = max(max_uid, real_uid)
                        continue

                    flags = _extract_flags_from_fetch_response(fetch_resp)
                    is_read = r"\Seen" in flags

                    raw = _extract_email_body(fetch_resp)
                    if not raw:
                        continue

                    parsed = email.message_from_bytes(raw)
                    del raw

                    async with self._factory() as session:
                        stored = await _persist_imap_message(
                            session=session,
                            parsed=parsed,
                            imap_uid=real_uid,
                            folder=folder,
                            company_id=self._company_id,
                            user_id=cast(uuid.UUID, self._user_id),
                            is_read=is_read,
                        )
                        await session.commit()

                    del parsed
                    if stored:
                        log.info(f"ImapFolderPoller [{folder.value}]: saved uid={real_uid}")
                    max_uid = max(max_uid, real_uid)

                except Exception as e:
                    log.exception(f"ImapFolderPoller [{folder.value}]: error on seq={seq}: {e}")

            if max_uid > last_uid:
                await self._update_sync_state(folder, max_uid)

        except Exception as e:
            log.exception(f"ImapFolderPoller [{folder.value}]: folder sync failed: {e}")

    async def _fetch_with_retry(
        self,
        client: aioimaplib.IMAP4_SSL,
        seq: str,
        folder_name: str,
        max_retries: int = 3,
    ) -> list[Any] | None:  # noqa: ANN401
        """Выполняет fetch с повторными попытками при таймаутах и ошибках соединения."""

        for attempt in range(max_retries):
            try:
                fetch_resp: list[Any] = await client.fetch(seq, "(UID FLAGS BODY.PEEK[])")
                return fetch_resp
            except aioimaplib.aioimaplib.CommandTimeout:
                log.warning(f"ImapFolderPoller [{folder_name}]: fetch timeout on seq={seq}, attempt {attempt + 1}/{max_retries}")
                if attempt < max_retries - 1:
                    delay = 2 ** (attempt + 1)
                    log.info(f"ImapFolderPoller [{folder_name}]: retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    log.error(f"ImapFolderPoller [{folder_name}]: fetch failed after {max_retries} attempts on seq={seq}")
            except Exception as e:
                error_str = str(e).lower()

                if "closed" in error_str or "ssl" in error_str:
                    log.warning(f"ImapFolderPoller [{folder_name}]: connection closed on seq={seq}, re-raising to trigger reconnect")
                    raise

                log.warning(f"ImapFolderPoller [{folder_name}]: error on seq={seq}, attempt {attempt + 1}/{max_retries}: {e}")
                if attempt < max_retries - 1:
                    delay = 2 ** (attempt + 1)
                    await asyncio.sleep(delay)
                else:
                    log.error(f"ImapFolderPoller [{folder_name}]: fetch failed after {max_retries} attempts on seq={seq}")

        log.warning(f"ImapFolderPoller [{folder_name}]: skipping seq={seq} after {max_retries} failed attempts")
        return None

    async def _get_last_uid(self, folder: MailFolder) -> int:
        async with self._factory() as session:
            state = await session.scalar(
                select(MailSyncState).where(
                    MailSyncState.user_id == self._user_id,
                    MailSyncState.company_id == self._company_id,
                    MailSyncState.folder == folder.value,
                )
            )
        return state.last_synced_uid if state else 0

    async def _update_sync_state(self, folder: MailFolder, uid: int) -> None:
        async with self._factory() as session:
            state = await session.scalar(
                select(MailSyncState).where(
                    MailSyncState.user_id == self._user_id,
                    MailSyncState.company_id == self._company_id,
                    MailSyncState.folder == folder.value,
                )
            )
            if not state:
                state = MailSyncState(
                    user_id=self._user_id,
                    company_id=self._company_id,
                    folder=folder.value,
                    last_synced_uid=uid,
                    last_synced_at=datetime.now(UTC),
                )
                session.add(state)
            else:
                state.last_synced_uid = max(state.last_synced_uid, uid)
                state.last_synced_at = datetime.now(UTC)
            await session.commit()

    async def _resolve_user_id(self) -> None:
        from src.app.services.user.models import User

        async with self._factory() as session:
            user = await session.scalar(
                select(User).where(
                    User.email == app_settings.ADMIN_EMAIL,
                )
            )
        if user is None:
            raise RuntimeError(f"No user found for email={app_settings.ADMIN_EMAIL}")
        self._user_id = user.id


class ImapSyncer:
    def __init__(
        self,
        db: AsyncSession,
        company_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        self._db = db
        self._company_id = company_id
        self._user_id = user_id

    async def sync_folder(self, folder: MailFolder, days_history: int | None = None) -> MailSyncResult:
        imap_folder_name = _IMAP_FOLDER_MAP.get(folder, "INBOX")
        fetched = skipped = errors = 0

        log.info(f"ImapSyncer [{folder.value}]: sync start, days_history={days_history}")

        client = aioimaplib.IMAP4_SSL(
            host=app_settings.MAIL_IMAP_HOST,
            port=app_settings.MAIL_IMAP_PORT,
            timeout=30,
        )
        try:
            await client.wait_hello_from_server()
            await client.login(app_settings.MAIL_EMAIL, app_settings.MAIL_PASSWORD)

            # SELECT с fallback
            selected = False
            candidates = _IMAP_FOLDER_FALLBACKS.get(folder, [imap_folder_name])
            for name in candidates:
                sel_resp = await client.select(name)
                if sel_resp[0] == "OK":
                    selected = True
                    log.info(f"ImapSyncer [{folder.value}]: SELECT {name!r} OK")
                    break

            if not selected:
                log.warning(f"ImapSyncer [{folder.value}]: could not SELECT folder")
                return MailSyncResult(folder=folder, fetched=0, skipped=0, errors=1, synced_at=datetime.now(UTC))

            if days_history:
                date_str = (datetime.now(UTC) - timedelta(days=days_history)).strftime("%d-%b-%Y")
                search_cmd = f"SINCE {date_str}"
            else:
                search_cmd = "ALL"

            resp = await client.search(search_cmd)
            if resp[0] != "OK" or not resp[1][0]:
                return MailSyncResult(folder=folder, fetched=0, skipped=0, errors=0, synced_at=datetime.now(UTC))

            seq_list = [s for s in resp[1][0].split() if s]
            log.info(f"ImapSyncer [{folder.value}]: {len(seq_list)} messages to process")

            fetched_uids: list[int] = []

            for seq in seq_list:
                seq_str = seq.decode() if isinstance(seq, bytes) else str(seq)
                try:
                    f_resp = await client.fetch(seq_str, "(UID FLAGS BODY.PEEK[])")
                    if f_resp[0] != "OK":
                        errors += 1
                        continue

                    real_uid = _extract_uid_from_fetch_response(f_resp)
                    if real_uid is None:
                        errors += 1
                        continue

                    flags = _extract_flags_from_fetch_response(f_resp)
                    is_read = r"\Seen" in flags

                    raw = _extract_email_body(f_resp)
                    if not raw:
                        errors += 1
                        continue

                    parsed = email.message_from_bytes(raw)
                    del raw

                    stored = await _persist_imap_message(
                        session=self._db,
                        parsed=parsed,
                        imap_uid=real_uid,
                        folder=folder,
                        company_id=self._company_id,
                        user_id=self._user_id,
                        is_read=is_read,
                    )

                    del parsed

                    fetched_uids.append(real_uid)
                    if stored:
                        fetched += 1
                        log.info(f"ImapSyncer [{folder.value}]: saved uid={real_uid}")
                    else:
                        skipped += 1

                except Exception as e:
                    log.exception(f"ImapSyncer [{folder.value}]: error on seq={seq_str}: {e}")
                    errors += 1

            await self._db.commit()

            if fetched_uids:
                max_uid = max(fetched_uids)
                state = await self._db.scalar(
                    select(MailSyncState).where(
                        MailSyncState.user_id == self._user_id,
                        MailSyncState.company_id == self._company_id,
                        MailSyncState.folder == folder.value,
                    )
                )
                if not state:
                    state = MailSyncState(
                        user_id=self._user_id,
                        company_id=self._company_id,
                        folder=folder.value,
                        last_synced_uid=max_uid,
                        last_synced_at=datetime.now(UTC),
                    )
                    self._db.add(state)
                else:
                    state.last_synced_uid = max_uid
                    state.last_synced_at = datetime.now(UTC)
                await self._db.commit()

        except Exception as sync_err:
            log.exception(f"ImapSyncer [{folder.value}]: sync error: {sync_err}")
            errors += 1
        finally:
            try:
                await client.logout()
            except Exception:
                pass

        return MailSyncResult(
            folder=folder,
            fetched=fetched,
            skipped=skipped,
            errors=errors,
            synced_at=datetime.now(UTC),
        )


# ---------------------------------------------------------------------------
# Утилиты парсинга IMAP-ответов
# ---------------------------------------------------------------------------


def _extract_uidvalidity(select_response: Any) -> str | None:  # noqa: ANN401
    try:
        for line in select_response[1]:
            if isinstance(line, bytes) and b"UIDVALIDITY" in line:
                match = re.search(r"UIDVALIDITY (\d+)", line.decode())
                if match:
                    return match.group(1)
    except Exception:
        pass
    return None


def _extract_flags_from_fetch_response(fetch_response: list[Any]) -> set[str]:
    try:
        data = fetch_response[1]
        if not isinstance(data, list):
            return set()
        for item in data:
            if isinstance(item, bytes):
                match = re.search(rb"FLAGS\s*\(([^)]*)\)", item)
                if match:
                    raw = match.group(1).decode(errors="replace")
                    return {f.strip() for f in raw.split() if f.strip()}
    except Exception:
        pass
    return set()


def _extract_email_body(fetch_response: list[Any]) -> bytes | None:
    if not fetch_response:
        return None

    all_chunks = []

    def walk_response(item: object) -> None:
        if isinstance(item, (bytes, bytearray)):
            all_chunks.append(bytes(item))
        elif isinstance(item, (list, tuple)):
            for sub_item in item:
                walk_response(sub_item)

    walk_response(fetch_response)

    if not all_chunks:
        return None

    for chunk in sorted(all_chunks, key=len, reverse=True):
        head = chunk[:1000].lower()
        if any(h in head for h in [b"from:", b"subject:", b"content-type:", b"received:"]):
            return chunk

    return max(all_chunks, key=len)


_extract_yandex_email_body = _extract_email_body


async def _persist_imap_message(
    *,
    session: AsyncSession,
    parsed: email.message.Message,
    imap_uid: int,
    folder: MailFolder,
    company_id: uuid.UUID,
    user_id: uuid.UUID,
    is_read: bool = False,
) -> bool:
    try:
        message_id_header = (parsed.get("Message-ID") or "").strip()
        subject_preview = _decode_header_value(parsed.get("Subject", ""))[:80]

        log.debug(f"_persist_imap_message: uid={imap_uid}, folder={folder.value}, msg_id={message_id_header!r}, subject={subject_preview!r}")

        if message_id_header:
            existing = await session.scalar(
                select(MailMessage).where(
                    MailMessage.external_message_id == message_id_header,
                    MailMessage.company_id == company_id,
                )
            )
            if existing:
                if is_read and not existing.is_read:
                    existing.is_read = True
                    await session.flush()
                    log.info(f"_persist_imap_message: marked read, id={existing.id}")
                log.info(f"Message {message_id_header} already exists, skipping")
                return True

        if not message_id_header:
            existing_by_uid = await session.scalar(
                select(MailMessage).where(
                    MailMessage.imap_uid == imap_uid,
                    MailMessage.folder == folder,
                    MailMessage.company_id == company_id,
                )
            )
            if existing_by_uid:
                if is_read and not existing_by_uid.is_read:
                    existing_by_uid.is_read = True
                    await session.flush()
                return False

        subject = _decode_header_value(parsed.get("Subject", ""))
        sender_name, sender_email = _parse_address(parsed.get("From", ""))

        sent_at = None
        date_str = parsed.get("Date")
        if date_str:
            try:
                from email.utils import parsedate_to_datetime

                dt = parsedate_to_datetime(date_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                sent_at = dt
            except Exception:
                log.warning(f"_persist_imap_message: bad date {date_str!r}")

        thread_id, parent_id = await _resolve_thread(session, parsed, company_id)

        log.info(
            f"_persist_imap_message: SAVING uid={imap_uid}, folder={folder.value}, "
            f"from={sender_email!r}, thread_id={thread_id}, parent_id={parent_id}"
        )

        body_text, body_html, attachments_raw = _extract_body_and_attachments(parsed)
        message_id = uuid.uuid4()

        msg = MailMessage(
            id=message_id,
            company_id=company_id,
            external_message_id=message_id_header or None,
            thread_id=thread_id,
            parent_id=parent_id,
            user_id=user_id,
            sender_email=sender_email,
            sender_name=sender_name,
            subject=subject,
            folder=folder,
            message_type=MailMessageType.INCOMING,
            status=MailMessageStatus.DELIVERED,
            is_read=is_read,
            imap_uid=imap_uid,
            sent_at=sent_at,
        )
        session.add(msg)
        await session.flush()

        session.add(MailContent(message_id=message_id, body_text=body_text, body_html=body_html))

        for filename, content_type, payload_bytes in attachments_raw:
            att_id = uuid.uuid4()
            s3_key = f"attachments/{company_id}/{message_id}/{att_id}_{filename}"
            file_size = len(payload_bytes)
            try:
                await s3_storage.upload_file(file_obj=payload_bytes, object_key=s3_key, content_type=content_type)
            except Exception:
                s3_key = ""
            finally:
                del payload_bytes

            session.add(
                MailAttachment(
                    id=att_id,
                    company_id=company_id,
                    mail_message_id=message_id,
                    s3_key=s3_key,
                    s3_bucket=app_settings.S3_BUCKET_NAME,
                    filename=filename,
                    content_type=content_type,
                    file_size=file_size,
                    attachment_id=str(att_id),
                )
            )

        return True
    except Exception:
        log.exception("_persist_imap_message failed")
        return False


def _decode_header_value(raw: Any) -> str:  # noqa: ANN401
    from email.header import decode_header, make_header

    try:
        return str(make_header(decode_header(str(raw))))
    except Exception:
        return str(raw)


def _parse_address(raw: str) -> tuple[str | None, str]:
    name, addr = parseaddr(raw)
    return (_decode_header_value(name) or None, addr.lower())


def _extract_body_and_attachments(
    msg: email.message.Message,
) -> tuple[str | None, str | None, list[tuple[str, str, bytes]]]:
    body_text: str | None = None
    body_html: str | None = None
    attachments: list[tuple[str, str, bytes]] = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition") or "")

        if "attachment" in disposition or part.get_filename():
            filename = _decode_header_value(part.get_filename() or "attachment")
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                attachments.append((filename, content_type, payload))
        elif content_type == "text/plain" and body_text is None:
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                body_text = payload.decode(charset, errors="replace")
        elif content_type == "text/html" and body_html is None:
            charset = part.get_content_charset() or "utf-8"
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes):
                body_html = payload.decode(charset, errors="replace")

    return body_text, body_html, attachments


def _extract_uid_from_fetch_response(fetch_response: list[Any]) -> int | None:
    try:
        data = fetch_response[1]
        if not isinstance(data, list):
            return None
        for item in data:
            if isinstance(item, bytes):
                match = re.search(rb"UID\s+(\d+)", item)
                if match:
                    return int(match.group(1))
    except Exception:
        pass
    return None
