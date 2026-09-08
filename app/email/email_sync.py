from __future__ import annotations

import codecs
import email
import logging
from typing import Optional, List, Tuple

logger = logging.getLogger(__name__)


def safe_header(value: str) -> str:
    """
    Safely decode email header with multiple encodings.
    
    Args:
        value: Header value to decode
        
    Returns:
        Decoded header string
    """
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        decoded = []
        for text, charset in parts:
            if isinstance(text, bytes):
                try:
                    decoded.append(text.decode(charset or "utf-8", errors="replace"))
                except (LookupError, TypeError):
                    # LookupError: unknown charset
                    # TypeError: invalid charset type
                    decoded.append(text.decode("utf-8", errors="replace"))
            else:
                decoded.append(text)
        return "".join(decoded)
    except Exception as e:
        logger.warning(f"Failed to decode header, returning raw value: {e}")
        return value


def decode_imap_utf7(name: bytes | str) -> str:
    """
    Decode IMAP UTF-7 mailbox name to standard string.
    
    Args:
        name: Mailbox name in bytes or string
        
    Returns:
        Decoded mailbox name
    """
    if not name:
        return ""
    try:
        s = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else str(name)
        try:
            return codecs.decode(s.encode("ascii", errors="replace"), "imap4-utf-7")
        except Exception:
            try:
                return codecs.decode(s, "imap4-utf-7")
            except Exception:
                return s
    except Exception as e:
        logger.warning(f"Failed to decode IMAP UTF-7, returning string representation: {e}")
        return str(name)


def encode_imap_utf7(name: str) -> str:
    """
    Encode string to IMAP UTF-7 format for mailbox names.
    
    Args:
        name: Mailbox name to encode
        
    Returns:
        IMAP UTF-7 encoded string
    """
    if not name:
        return ""
    try:
        return codecs.encode(name, "imap4-utf-7").decode("ascii", errors="replace")
    except Exception as e:
        logger.warning(f"Failed to encode IMAP UTF-7, returning original name: {e}")
        return name


def extract_folder_name(raw: bytes) -> str:
    """
    Extract folder name from IMAP LIST response.
    
    Args:
        raw: Raw IMAP response bytes
        
    Returns:
        Extracted folder name
    """
    if not raw:
        return ""
    try:
        line = raw.decode(errors="replace").strip()
        if '"' in line:
            parts = line.split('"')
            if len(parts) >= 2:
                return parts[-2].strip()
        tokens = line.split()
        return tokens[-1].strip() if tokens else ""
    except Exception as e:
        logger.warning(f"Failed to extract folder name: {e}")
        return ""


def extract_separator(raw: bytes) -> str:
    """
    Extract folder hierarchy separator from IMAP LIST response.
    
    Args:
        raw: Raw IMAP response bytes
        
    Returns:
        Folder separator character or default '/'
    """
    try:
        line = raw.decode(errors="replace")
        if '"' in line:
            parts = line.split('"')
            if len(parts) >= 3:
                return parts[1]
        return "/"
    except Exception as e:
        logger.warning(f"Failed to extract separator, using default '/': {e}")
        return "/"


def extract_flags(raw: bytes) -> str:
    """
    Extract mailbox flags from IMAP LIST response.
    
    Args:
        raw: Raw IMAP response bytes
        
    Returns:
        Comma-separated flags string
    """
    try:
        line = raw.decode(errors="replace")
        if "(" in line and ")" in line:
            return line.split(")")[0].replace("(", "").strip()
        return ""
    except Exception as e:
        logger.warning(f"Failed to extract flags: {e}")
        return ""


def imap_list_folders(imap) -> List[str]:
    """
    List all folders from IMAP account with multiple fallback strategies.
    
    Args:
        imap: IMAP connection object
        
    Returns:
        List of folder names
    """
    attempts = [
        ("INBOX", "*"),
        ("INBOX", "%"),
        ("", "*"),
        ("", "%"),
    ]
    for args in attempts:
        try:
            typ, boxes = imap.list(*args)
            if typ == "OK" and boxes:
                return [extract_folder_name(b) for b in boxes]
        except Exception as e:
            logger.debug(f"IMAP list attempt {args} failed: {e}")
    return []


def imap_safe_select(imap, folder: str) -> bool:
    """
    Select IMAP folder with multiple fallback attempts.
    
    Args:
        imap: IMAP connection object
        folder: Folder name to select
        
    Returns:
        True if selection succeeded, False otherwise
    """
    if not folder:
        return False
    
    attempts = [
        (folder, True),
        (folder, False),
        (folder, None),
    ]
    for f, readonly in attempts:
        try:
            if readonly is None:
                status, _ = imap.select(f)
            else:
                status, _ = imap.select(f, readonly=readonly)
            if status == "OK":
                return True
        except Exception as e:
            logger.debug(f"IMAP select attempt {f} (readonly={readonly}) failed: {e}")
    return False


def imap_fetch_headers(imap, uid: str) -> Optional[email.message.Message]:
    """
    Fetch email headers only (faster than full message).
    
    Args:
        imap: IMAP connection object
        uid: Message UID
        
    Returns:
        Parsed email message or None if fetch fails
    """
    if not uid:
        return None
    
    try:
        typ, msg_data = imap.fetch(uid, "(BODY.PEEK[HEADER])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        return email.message_from_bytes(msg_data[0][1])
    except Exception as e:
        logger.warning(f"Failed to fetch headers for UID {uid}: {e}")
        return None


def imap_fetch_full(imap, uid: str) -> Optional[email.message.Message]:
    """
    Fetch complete email message.
    
    Args:
        imap: IMAP connection object
        uid: Message UID
        
    Returns:
        Parsed email message or None if fetch fails
    """
    if not uid:
        return None
    
    try:
        typ, msg_data = imap.fetch(uid, "(BODY[])")
        if typ != "OK" or not msg_data or not msg_data[0]:
            return None
        return email.message_from_bytes(msg_data[0][1])
    except Exception as e:
        logger.warning(f"Failed to fetch full message for UID {uid}: {e}")
        return None


def imap_search_uids(imap) -> List[str]:
    """
    Search for all messages in current folder.
    
    Args:
        imap: IMAP connection object
        
    Returns:
        List of message UIDs
    """
    try:
        typ, data = imap.search(None, "ALL")
        if typ != "OK":
            logger.warning("IMAP search failed")
            return []
        uid_list = data[0].split() if data[0] else []
        return [uid.decode() if isinstance(uid, bytes) else uid for uid in uid_list]
    except Exception as e:
        logger.error(f"Failed to search UIDs: {e}")
        return []


def imap_mark_read(imap, uid: str) -> bool:
    """
    Mark message as read.
    
    Args:
        imap: IMAP connection object
        uid: Message UID
        
    Returns:
        True if successful, False otherwise
    """
    if not uid:
        return False
    
    try:
        typ, _ = imap.store(uid, "+FLAGS", r"(\Seen)")
        return typ == "OK"
    except Exception as e:
        logger.warning(f"Failed to mark UID {uid} as read: {e}")
        return False


def imap_mark_unread(imap, uid: str) -> bool:
    """
    Mark message as unread.
    
    Args:
        imap: IMAP connection object
        uid: Message UID
        
    Returns:
        True if successful, False otherwise
    """
    if not uid:
        return False
    
    try:
        typ, _ = imap.store(uid, "-FLAGS", r"(\Seen)")
        return typ == "OK"
    except Exception as e:
        logger.warning(f"Failed to mark UID {uid} as unread: {e}")
        return False


def imap_move_message(imap, uid: str, target_folder: str) -> bool:
    """
    Move message to target folder.
    
    Args:
        imap: IMAP connection object
        uid: Message UID
        target_folder: Destination folder name
        
    Returns:
        True if successful, False otherwise
    """
    if not uid or not target_folder:
        return False
    
    try:
        typ, _ = imap.copy(uid, target_folder)
        if typ != "OK":
            logger.warning(f"Failed to copy UID {uid} to {target_folder}")
            return False
        
        # Mark original as deleted
        imap.store(uid, "+FLAGS", r"(\Deleted)")
        imap.expunge()
        return True
    except Exception as e:
        logger.error(f"Failed to move UID {uid} to {target_folder}: {e}")
        return False


def imap_create_folder(imap, folder: str) -> bool:
    """
    Create new folder in IMAP account.
    
    Args:
        imap: IMAP connection object
        folder: Folder name to create
        
    Returns:
        True if successful, False otherwise
    """
    if not folder:
        return False
    
    try:
        typ, _ = imap.create(folder)
        return typ == "OK"
    except Exception as e:
        logger.warning(f"Failed to create folder {folder}: {e}")
        return False


def imap_delete_folder(imap, folder: str) -> bool:
    """
    Delete folder from IMAP account.
    
    Args:
        imap: IMAP connection object
        folder: Folder name to delete
        
    Returns:
        True if successful, False otherwise
    """
    if not folder:
        return False
    
    try:
        typ, _ = imap.delete(folder)
        return typ == "OK"
    except Exception as e:
        logger.warning(f"Failed to delete folder {folder}: {e}")
        return False


def sync_folders(imap, db_folders: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """
    Synchronize IMAP folders with database state.
    
    Args:
        imap: IMAP connection object
        db_folders: List of folders currently in database
        
    Returns:
        Tuple of (all_imap_folders, new_folders, deleted_folders)
    """
    mailboxes = []
    for args in [
        ("INBOX", "*"),
        ("INBOX", "%"),
        ("", "*"),
        ("", "%"),
    ]:
        try:
            typ, boxes = imap.list(*args)
            if typ == "OK" and boxes:
                mailboxes = boxes
                break
        except Exception as e:
            logger.debug(f"IMAP list attempt {args} failed: {e}")

    imap_folders = [extract_folder_name(m) for m in mailboxes if m]
    imap_set = set(f for f in imap_folders if f)
    db_set = set(f for f in db_folders if f)
    
    new_folders = list(imap_set - db_set)
    deleted_folders = list(db_set - imap_set)
    
    return imap_folders, new_folders, deleted_folders
