"""A wire-accurate fake RAD server for the integration suite.

It speaks what ``docs/fonte-rad.md`` records: the JSON envelope with ``temErro`` /
``SolicitarCaptcha`` / ``dados``, ``$&&*`` and ``$&`` separators with a trailing row
separator, ``<spanOrder>`` sort keys, all three statuses in every listing, one download path
for every category, ``text/html`` on binary responses — and the on-demand variance of
structured packages: the generated reading PDF inside a ZIP differs on every download, the
other members never do.

The server is driven by a mutable :class:`Scenario` the test edits between runs: documents
appear, change status, or vanish, and the next listing tells the story the test needs told.
"""

from __future__ import annotations

import io
import json
import threading
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tests.rad import envelope, payload, row

SEARCH_PATH = "/frmConsultaExternaCVM.aspx/ListarDocumentos"
DOWNLOAD_PATH = "/frmDownloadDocumento.aspx"

#: A plausible XML member for structured packages: stable across downloads.
XML_MEMBER = b'<?xml version="1.0"?><Documento><CodigoCvm>9512</CodigoCvm></Documento>'


@dataclass
class ServedDocument:
    """One publication the fake source serves, in neutral terms; the wire is rendered here."""

    document_id: int
    version: int = 1
    protocol: str = ""
    cvm_code: str = "009512"
    legal_name: str = "PETROLEO BRASILEIRO S.A. PETROBRAS"
    category: str = "Fato Relevante"
    status: str = "Ativo"
    delivery: date = date(2026, 8, 24)
    subject: str = "Assunto de teste"
    kind: str = "pdf"  # "pdf" | "zip" | "html" — html is the error page with HTTP 200

    def __post_init__(self) -> None:
        if not self.protocol:
            self.protocol = str(1_500_000 + self.document_id)

    @property
    def wire_cvm_code(self) -> str:
        """``009512`` -> ``00951-2``, the hyphenated spelling of listing field 0."""
        return f"{self.cvm_code[:5]}-{self.cvm_code[5]}"

    def wire_row(self) -> str:
        stamp = self.delivery.strftime("%Y%m%d")
        display = self.delivery.strftime("%d/%m/%Y")
        return row(
            cvm_code=self.wire_cvm_code,
            legal_name=self.legal_name,
            category=self.category,
            species=self.subject,
            reference=stamp,
            reference_display=display,
            delivery=stamp,
            delivery_display=f"{display} 15:37",
            status=self.status,
            version=self.version,
            subject=self.subject,
            document_id=self.document_id,
            protocol=self.protocol,
        )


@dataclass
class Scenario:
    """What the server answers right now. Tests mutate it between runs."""

    documents: list[ServedDocument] = field(default_factory=list)
    captcha: bool = False
    #: Listings left to fail with ``temErro`` before answering again. A very large number
    #: is "the backend is down and stays down".
    failing_listings: int = 0

    def get(self, document_id: int, version: int) -> ServedDocument | None:
        return next(
            (
                document
                for document in self.documents
                if document.document_id == document_id and document.version == version
            ),
            None,
        )


class FakeRad:
    """The server around one :class:`Scenario`, bound to an ephemeral localhost port."""

    def __init__(self) -> None:
        self.scenario = Scenario()
        self.listing_requests: list[date] = []
        self.download_requests: list[tuple[int, int]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.rad = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> FakeRad:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    @property
    def rad(self) -> FakeRad:
        return self.server.rad  # type: ignore[attr-defined]

    def do_POST(self) -> None:
        if urlparse(self.path).path != SEARCH_PATH:
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        day = datetime.strptime(request["dataDe"], "%d/%m/%Y").date()
        self.rad.listing_requests.append(day)

        scenario = self.rad.scenario
        if scenario.captcha:
            answer = envelope(captcha="S")
        elif scenario.failing_listings > 0:
            scenario.failing_listings -= 1
            answer = envelope(tem_erro=True, msg_erro="Ocorreu um erro no servidor.")
        else:
            rows = [
                document.wire_row()
                for document in scenario.documents
                if document.delivery == day
            ]
            answer = envelope(payload(*rows))
        self._respond(json.dumps(answer).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != DOWNLOAD_PATH:
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        document_id = int(query["numSequencia"][0])
        version = int(query["numVersao"][0])
        self.rad.download_requests.append((document_id, version))

        document = self.rad.scenario.get(document_id, version)
        if document is None:
            # The source's own failure mode: an error page, with HTTP 200 and a lying type.
            self._respond(b"<html><body>erro</body></html>", "text/html")
            return
        serial = self.rad.download_requests.count((document_id, version))
        if document.kind == "zip":
            content = _structured_package(document, serial)
        elif document.kind == "html":
            content = b"<html><body>erro</body></html>"
        else:
            content = _pdf(document, b"stable")
        # ``Content-Type`` always lies on this source; the sniffing must not need it.
        self._respond(content, "text/html")

    def _respond(self, content: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        pass  # the suite's output belongs to pytest, not to every request


def _pdf(document: ServedDocument, variant: bytes) -> bytes:
    return b"%PDF-1.7\n% " + str(document.document_id).encode() + b" " + variant + b"\n%%EOF\n"


def _structured_package(document: ServedDocument, serial: int) -> bytes:
    """A structured delivery: stable members plus the generated copy that never repeats.

    The generated PDF carries the generation instant in its name and different bytes in its
    body — which is exactly why the pipeline imposes names and the manifest marks stability.
    """
    buffer = io.BytesIO()
    generated = f"{document.document_id}_{int(document.cvm_code)}_2026082415{serial:04d}.pdf"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{document.cvm_code}ITR30-06-2026v{document.version}.xml", XML_MEMBER)
        archive.writestr(generated, _pdf(document, f"generated {serial}".encode()))
    return buffer.getvalue()
