"""Wire-format builders and recorded samples for the RAD contract tests.

The builders assemble payloads the way the source does — literal separators, a trailing row
separator, ``<spanOrder>`` sort keys — so unit tests can vary one thing at a time. The
``RECORDED_*`` constants are real rows captured verbatim from a full-market listing of
2026-08-21 (requested 2026-08-24); they are what pins the parser to the wire that actually
exists, not to the wire the builders imagine.
"""

from __future__ import annotations

from typing import Any

ROW_SEPARATOR = "$&&*"
FIELD_SEPARATOR = "$&"


def action_icons(
    document_id: int = 1084789,
    version: int = 1,
    protocol: str = "1560083",
    desc_tipo: str = "IPE",
    *,
    quoted: bool = True,
) -> str:
    """Field 10: the icons HTML around the ``OpenDownloadDocumentos`` call.

    The page quotes all four arguments; ``quoted=False`` reproduces the unquoted spelling
    of the page's own JavaScript source, which the parser also tolerates.
    """
    if quoted:
        call = f"OpenDownloadDocumentos('{document_id}','{version}','{protocol}','{desc_tipo}')"
    else:
        call = f"OpenDownloadDocumentos({document_id}, {version}, '{protocol}', '{desc_tipo}')"
    return (
        "<i class='fi-page-search' id='VisualizarDocumento' style='cursor:pointer;' "
        "onclick=OpenPopUpVer('frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega="
        f"{protocol}') title='Visualizar o Documento'> </i>"
        f"<i class='fi-download' style='cursor:pointer;' title='Download' onclick={call}> </i>"
    )


def row(
    *,
    cvm_code: str = "00951-2",
    legal_name: str = "PETROLEO BRASILEIRO S.A. PETROBRAS",
    category: str = "Fato Relevante",
    doc_type: str = " - ",
    species: str = "Petrobras informa sobre remuneração aos acionistas",
    reference: str = "20260821",
    reference_display: str = "21/08/2026",
    delivery: str = "20260821",
    delivery_display: str = "21/08/2026 15:37",
    status: str = "Ativo",
    version: int = 1,
    modality: str = "AP",
    actions: str | None = None,
    subject: str = "Petrobras informa sobre remuneração aos acionistas",
    document_id: int = 1084789,
    protocol: str = "1560083",
) -> str:
    """One twelve-field wire row. Semantic arguments; malformed rows are built by hand."""
    if actions is None:
        actions = action_icons(document_id=document_id, version=version, protocol=protocol)
    fields = [
        cvm_code,
        legal_name,
        category,
        doc_type,
        f"<spanOrder>{species}</spanOrder> - ",
        f"<spanOrder>{reference}</spanOrder> {reference_display}",
        f"<spanOrder>{delivery}</spanOrder> {delivery_display}",
        status,
        str(version),
        modality,
        actions,
        subject,
    ]
    return FIELD_SEPARATOR.join(fields)


def payload(*rows: str) -> str:
    """A ``dados`` string: rows joined and terminated by the row separator."""
    return "".join(part + ROW_SEPARATOR for part in rows)


def envelope(
    dados: str = "",
    *,
    tem_erro: bool = False,
    msg_erro: str = "",
    captcha: str = "N",
) -> dict[str, Any]:
    """The JSON envelope, exactly as the PageMethod answers it."""
    return {
        "d": {
            "__type": "frmConsultaExternaCVM+RetornoTelaConsultaExterna",
            "temErro": tem_erro,
            "expirouSessao": False,
            "msgErro": msg_erro,
            "SolicitarCaptcha": captcha,
            "dados": dados,
        }
    }


# --- Recorded rows, captured verbatim on 2026-08-24 from the 2026-08-21 market day. ---

#: An active eventual filing: quoted download arguments, descTipo ``'IPE'``, a numeric
#: protocol, and the species text repeated in the field-4 sort key and in the subject.
RECORDED_ACTIVE = "00243-7$&AXIA ENERGIA S.A.$&Fato Relevante$& - $&<spanOrder>Processo Judicial movido pelo Estado do Piauí</spanOrder> - $&<spanOrder>20260821</spanOrder> 21/08/2026$&<spanOrder>20260821</spanOrder> 21/08/2026 09:37$&Ativo$&1$&AP$&<i class='fi-page-search' id='VisualizarDocumento' style='font-size: 18px;cursor:pointer;color:#0C7766;'  onclick=OpenPopUpVer('frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega=1560083') title='Visualizar o Documento'> </i><i class='fi-download' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Download' onclick=OpenDownloadDocumentos('1084789','1','1560083','IPE')> </i><i class='fi-info' style='font-size: 18px;cursor:pointer;color:#696969;' title='Documento não possui local de publicação.'> </i><i class='fi-clipboard-notes' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Exibir Protocolo de Entrega' onclick='exibirProtocoloPDF(1084789, \"IPE\")'</i>$&Processo Judicial movido pelo Estado do Piauí"  # noqa: E501

#: A structured document (ITR): empty species key, empty subject, a reference date in a
#: different month than the delivery, and the structured protocol shape.
RECORDED_STRUCTURED = "02592-5$&BIONEXO S.A.$&ITR - Informações Trimestrais$& - $&<spanOrder></spanOrder> - $&<spanOrder>20260630</spanOrder> 30/06/2026$&<spanOrder>20260821</spanOrder> 21/08/2026 22:20$&Ativo$&1$&AP$&<i class='fi-page-search' id='VisualizarDocumento' style='font-size: 18px;cursor:pointer;color:#0C7766;' onclick=OpenPopUpVer('frmGerenciaPaginaFRE.aspx?NumeroSequencialDocumento=161032&CodigoTipoInstituicao=1') title='Visualizar o Documento'> </i><i class='fi-download' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Download' onclick=OpenDownloadDocumentos('161032','1','025925ITR300620260100161032-78','ITR')> </i><i class='fi-info' style='font-size: 18px;cursor:pointer;color:#696969;' title='Documento não possui local de publicação.'> </i><i class='fi-clipboard-notes' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Exibir Protocolo de Entrega' onclick='exibirProtocoloPDF(161032, \"ENET\")'</i>$&"  # noqa: E501

#: An inactive (superseded) filing — the download call is present here too.
RECORDED_INACTIVE = "02001-0$&EQUATORIAL S.A.$&Comunicado ao Mercado$&Apresentações a analistas/agentes do mercado$&<spanOrder>Apresentação Site Visit CEEE-D</spanOrder> - $&<spanOrder>20260821</spanOrder> 21/08/2026$&<spanOrder>20260821</spanOrder> 21/08/2026 10:22$&Inativo$&1$&AP$&<i class='fi-page-search' id='VisualizarDocumento' style='font-size: 18px;cursor:pointer;color:#0C7766;'  onclick=OpenPopUpVer('frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega=1560098') title='Visualizar o Documento'> </i><i class='fi-download' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Download' onclick=OpenDownloadDocumentos('1084804','1','1560098','IPE')> </i><i class='fi-info' style='font-size: 18px;cursor:pointer;color:#696969;' title='Documento não possui local de publicação.'> </i><i class='fi-clipboard-notes' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Exibir Protocolo de Entrega' onclick='exibirProtocoloPDF(1084804, \"IPE\")'</i>$&Apresentação Site Visit CEEE-D"  # noqa: E501

#: A cancelled filing — mentioned in the inbox, never downloaded, still fully parseable.
RECORDED_CANCELLED = "02747-2$&CENTRAIS ELÉTRICAS DO NORTE DO BRASIL S.A$&Fato Relevante$& - $&<spanOrder>Processo Judicial movido pelo Estado do Piauí</spanOrder> - $&<spanOrder>20260821</spanOrder> 21/08/2026$&<spanOrder>20260821</spanOrder> 21/08/2026 09:00$&Cancelado$&1$&AP$&<i class='fi-page-search' id='VisualizarDocumento' style='font-size: 18px;cursor:pointer;color:#0C7766;'  onclick=OpenPopUpVer('frmExibirArquivoIPEExterno.aspx?NumeroProtocoloEntrega=1560076') title='Visualizar o Documento'> </i><i class='fi-download' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Download' onclick=OpenDownloadDocumentos('1084782','1','1560076','IPE')> </i><i class='fi-info' style='font-size: 18px;cursor:pointer;color:#696969;' title='Documento não possui local de publicação.'> </i><i class='fi-clipboard-notes' style='font-size: 18px;cursor:pointer;color:#0C7766;' title='Exibir Protocolo de Entrega' onclick='exibirProtocoloPDF(1084782, \"IPE\")'</i>$&Processo Judicial movido pelo Estado do Piauí"  # noqa: E501

RECORDED_ROWS = (
    RECORDED_ACTIVE,
    RECORDED_STRUCTURED,
    RECORDED_INACTIVE,
    RECORDED_CANCELLED,
)
