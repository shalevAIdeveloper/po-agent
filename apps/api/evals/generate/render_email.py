"""EML files with the stdlib (`policy.SMTP`: CRLF line ends, as mail is sent).

Deterministic: Date = the case's `received_at`, a fixed Message-ID and MIME boundary,
no `email.utils.make_msgid()` (it reads the clock and the host name). `From` is always
`meta.sender_email`.
"""

from email import policy
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime

from evals.generate.data import DISTRIBUTOR
from evals.generate.spec import Case, EmailDoc


def message_id(case: Case, number: int) -> str:
    return f"<eval-{case.seed}-{number}@{case.sender_email.rpartition('@')[2]}>"


def render(case: Case, doc: EmailDoc) -> bytes:
    message = EmailMessage(policy=policy.SMTP)
    local, _, domain = case.sender_email.rpartition("@")
    message["From"] = Address(doc.from_name, local, domain)
    message["To"] = doc.to or DISTRIBUTOR.buyer_email
    message["Subject"] = doc.subject
    message["Date"] = format_datetime(case.received_at)
    message["Message-ID"] = message_id(case, doc.message_no)
    if doc.in_reply_to is not None:
        message["In-Reply-To"] = message_id(case, doc.in_reply_to)
        message["References"] = message_id(case, doc.in_reply_to)
    if doc.plain is not None:
        message.set_content(doc.plain)
        if doc.html is not None:
            message.add_alternative(doc.html, subtype="html")
    elif doc.html is not None:
        message.set_content(doc.html, subtype="html")
    else:
        raise ValueError(f"{case.name}: an email needs a body")
    if message.is_multipart():
        message.set_boundary(f"=_eval_{case.name}")
    return message.as_bytes(policy=policy.SMTP)
