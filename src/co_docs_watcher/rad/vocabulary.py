"""The category vocabulary of the source, copied from the page.

This is the ``cboDocumentos`` combo of ``frmConsultaExternaCVM.aspx``, copied verbatim on
2026-08-24: 8 ``EST_*`` codes for structured documents and 524 ``IPE_*_*_*`` codes for
eventual filings (category_type_species). The labels are the source's own Portuguese wire
vocabulary — data that is not ours — with only their padding whitespace collapsed.

Phase 0 never filters by category on the server: discovery always sends ``ALL_DOCUMENTS``
and filters locally, because a wrong category code returns zero rows with no error —
indistinguishable from a quiet market. The trap goes further: the front silently expands
some codes before sending (``EST_2`` becomes ``EST_2,EST_8,EST_9``), so even a *correct*
code straight from this table is not proof the server will answer what the page would. This
table exists so that server-side filtering, if it is ever added, starts from copied codes
rather than deduced ones — and it must be re-verified against the page on that day, not
trusted indefinitely.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = ["ALL_DOCUMENTS", "EVENTUAL_DOCUMENTS", "STRUCTURED_DOCUMENTS"]

#: What discovery always requests: every structured and every eventual document.
ALL_DOCUMENTS = "EST_-1,IPE_-1_-1_-1"

#: The 8 ``EST_*`` codes, copied 2026-08-24.
STRUCTURED_DOCUMENTS: MappingProxyType[str, str] = MappingProxyType({
    'EST_-1': 'TODOS os Documentos Estruturados',
    'EST_3': 'ITR - Informações Trimestrais',
    'EST_2': 'FRE - Formulário de Referência',
    'EST_11': 'Informe do Código de Governança',
    'EST_1': 'FCA - Formulário Cadastral',
    'EST_4': 'DFP - Demonstrações Financeiras Padronizadas',
    'EST_6': 'Informe Trimestral de Securitizadora',
    'EST_13': 'IAN - Informações Anuais',
})

#: The 524 ``IPE_*_*_*`` codes (category_type_species), copied 2026-08-24.
EVENTUAL_DOCUMENTS: MappingProxyType[str, str] = MappingProxyType({
    'IPE_-1_-1_-1': 'TODOS os Documentos com Informações Eventuais',
    'IPE_44_-1_-1': 'Acordo de Acionistas | TODOS | TODAS',
    'IPE_1_-1_-1': 'Assembleia | TODOS | TODAS',
    'IPE_1_-1_4': 'Assembleia | TODOS | Ata',
    'IPE_1_-1_54': 'Assembleia | TODOS | Boletim de voto a distância',
    'IPE_1_-1_28': 'Assembleia | TODOS | Edital de Convocação',
    'IPE_1_-1_61': 'Assembleia | TODOS | Instrução de voto',
    'IPE_1_-1_6': 'Assembleia | TODOS | Justificação de Incorporação, Cisão ou Fusão',
    'IPE_1_-1_44': 'Assembleia | TODOS | Manual para participação',
    'IPE_1_-1_53': 'Assembleia | TODOS | Mapa final de votação',
    'IPE_1_-1_56': 'Assembleia | TODOS | Mapa Final de Votação Detalhado',
    'IPE_1_-1_72': 'Assembleia | TODOS | Mapa Final de Votação Resumido',
    'IPE_1_-1_57': 'Assembleia | TODOS | Mapa Final de Votação Sintético',
    'IPE_1_-1_52': 'Assembleia | TODOS | Mapa Sintético Consolidado',
    'IPE_1_-1_51': 'Assembleia | TODOS | Mapa Sintético do Depositário Central',
    'IPE_1_-1_50': 'Assembleia | TODOS | Mapa Sintético do Escriturador',
    'IPE_1_-1_73': 'Assembleia | TODOS | Mapa Sintético dos votos enviados diretamente à Companhia',
    'IPE_1_-1_41': 'Assembleia | TODOS | Material referente a pedidos públicos de procuração',
    'IPE_1_-1_2': 'Assembleia | TODOS | Proposta da Administração',
    'IPE_1_-1_32': 'Assembleia | TODOS | Proposta para a assembleia',
    'IPE_1_-1_5': 'Assembleia | TODOS | Protocolo de Incorporação, Cisão ou Fusão',
    'IPE_1_-1_55': 'Assembleia | TODOS | Protocolo e Justificação de Incorporação, Fusão ou Cisão',
    'IPE_1_-1_3': 'Assembleia | TODOS | Sumário das Decisões',
    'IPE_1_98_-1': 'Assembleia | AGCRA | TODAS',
    'IPE_1_98_4': 'Assembleia | AGCRA | Ata',
    'IPE_1_98_28': 'Assembleia | AGCRA | Edital de Convocação',
    'IPE_1_98_32': 'Assembleia | AGCRA | Proposta para a assembleia',
    'IPE_1_98_3': 'Assembleia | AGCRA | Sumário das Decisões',
    'IPE_1_97_-1': 'Assembleia | AGCRI | TODAS',
    'IPE_1_97_4': 'Assembleia | AGCRI | Ata',
    'IPE_1_97_28': 'Assembleia | AGCRI | Edital de Convocação',
    'IPE_1_97_32': 'Assembleia | AGCRI | Proposta para a assembleia',
    'IPE_1_97_3': 'Assembleia | AGCRI | Sumário das Decisões',
    'IPE_1_5_-1': 'Assembleia | AGDEB | TODAS',
    'IPE_1_5_4': 'Assembleia | AGDEB | Ata',
    'IPE_1_5_28': 'Assembleia | AGDEB | Edital de Convocação',
    'IPE_1_5_61': 'Assembleia | AGDEB | Instrução de voto',
    'IPE_1_5_44': 'Assembleia | AGDEB | Manual para participação',
    'IPE_1_5_2': 'Assembleia | AGDEB | Proposta da Administração',
    'IPE_1_5_3': 'Assembleia | AGDEB | Sumário das Decisões',
    'IPE_1_1_-1': 'Assembleia | AGE | TODAS',
    'IPE_1_1_4': 'Assembleia | AGE | Ata',
    'IPE_1_1_54': 'Assembleia | AGE | Boletim de voto a distância',
    'IPE_1_1_28': 'Assembleia | AGE | Edital de Convocação',
    'IPE_1_1_6': 'Assembleia | AGE | Justificação de Incorporação, Cisão ou Fusão',
    'IPE_1_1_44': 'Assembleia | AGE | Manual para participação',
    'IPE_1_1_53': 'Assembleia | AGE | Mapa final de votação',
    'IPE_1_1_56': 'Assembleia | AGE | Mapa Final de Votação Detalhado',
    'IPE_1_1_72': 'Assembleia | AGE | Mapa Final de Votação Resumido',
    'IPE_1_1_57': 'Assembleia | AGE | Mapa Final de Votação Sintético',
    'IPE_1_1_52': 'Assembleia | AGE | Mapa Sintético Consolidado',
    'IPE_1_1_51': 'Assembleia | AGE | Mapa Sintético do Depositário Central',
    'IPE_1_1_50': 'Assembleia | AGE | Mapa Sintético do Escriturador',
    'IPE_1_1_73': 'Assembleia | AGE | Mapa Sintético dos votos enviados diretamente à Companhia',
    'IPE_1_1_41': 'Assembleia | AGE | Material referente a pedidos públicos de procuração',
    'IPE_1_1_2': 'Assembleia | AGE | Proposta da Administração',
    'IPE_1_1_5': 'Assembleia | AGE | Protocolo de Incorporação, Cisão ou Fusão',
    'IPE_1_1_55': 'Assembleia | AGE | Protocolo e Justificação de Incorporação, Fusão ou Cisão',
    'IPE_1_1_3': 'Assembleia | AGE | Sumário das Decisões',
    'IPE_1_4_-1': 'Assembleia | AGESP | TODAS',
    'IPE_1_4_4': 'Assembleia | AGESP | Ata',
    'IPE_1_4_54': 'Assembleia | AGESP | Boletim de voto a distância',
    'IPE_1_4_28': 'Assembleia | AGESP | Edital de Convocação',
    'IPE_1_4_44': 'Assembleia | AGESP | Manual para participação',
    'IPE_1_4_53': 'Assembleia | AGESP | Mapa final de votação',
    'IPE_1_4_56': 'Assembleia | AGESP | Mapa Final de Votação Detalhado',
    'IPE_1_4_57': 'Assembleia | AGESP | Mapa Final de Votação Sintético',
    'IPE_1_4_52': 'Assembleia | AGESP | Mapa Sintético Consolidado',
    'IPE_1_4_50': 'Assembleia | AGESP | Mapa Sintético do Escriturador',
    'IPE_1_4_41': 'Assembleia | AGESP | Material referente a pedidos públicos de procuração',
    'IPE_1_4_2': 'Assembleia | AGESP | Proposta da Administração',
    'IPE_1_4_3': 'Assembleia | AGESP | Sumário das Decisões',
    'IPE_1_2_-1': 'Assembleia | AGO | TODAS',
    'IPE_1_2_4': 'Assembleia | AGO | Ata',
    'IPE_1_2_54': 'Assembleia | AGO | Boletim de voto a distância',
    'IPE_1_2_28': 'Assembleia | AGO | Edital de Convocação',
    'IPE_1_2_44': 'Assembleia | AGO | Manual para participação',
    'IPE_1_2_53': 'Assembleia | AGO | Mapa final de votação',
    'IPE_1_2_56': 'Assembleia | AGO | Mapa Final de Votação Detalhado',
    'IPE_1_2_72': 'Assembleia | AGO | Mapa Final de Votação Resumido',
    'IPE_1_2_57': 'Assembleia | AGO | Mapa Final de Votação Sintético',
    'IPE_1_2_52': 'Assembleia | AGO | Mapa Sintético Consolidado',
    'IPE_1_2_51': 'Assembleia | AGO | Mapa Sintético do Depositário Central',
    'IPE_1_2_50': 'Assembleia | AGO | Mapa Sintético do Escriturador',
    'IPE_1_2_73': 'Assembleia | AGO | Mapa Sintético dos votos enviados diretamente à Companhia',
    'IPE_1_2_41': 'Assembleia | AGO | Material referente a pedidos públicos de procuração',
    'IPE_1_2_2': 'Assembleia | AGO | Proposta da Administração',
    'IPE_1_2_3': 'Assembleia | AGO | Sumário das Decisões',
    'IPE_1_3_-1': 'Assembleia | AGO/E | TODAS',
    'IPE_1_3_4': 'Assembleia | AGO/E | Ata',
    'IPE_1_3_54': 'Assembleia | AGO/E | Boletim de voto a distância',
    'IPE_1_3_28': 'Assembleia | AGO/E | Edital de Convocação',
    'IPE_1_3_6': 'Assembleia | AGO/E | Justificação de Incorporação, Cisão ou Fusão',
    'IPE_1_3_44': 'Assembleia | AGO/E | Manual para participação',
    'IPE_1_3_53': 'Assembleia | AGO/E | Mapa final de votação',
    'IPE_1_3_56': 'Assembleia | AGO/E | Mapa Final de Votação Detalhado',
    'IPE_1_3_72': 'Assembleia | AGO/E | Mapa Final de Votação Resumido',
    'IPE_1_3_57': 'Assembleia | AGO/E | Mapa Final de Votação Sintético',
    'IPE_1_3_52': 'Assembleia | AGO/E | Mapa Sintético Consolidado',
    'IPE_1_3_51': 'Assembleia | AGO/E | Mapa Sintético do Depositário Central',
    'IPE_1_3_50': 'Assembleia | AGO/E | Mapa Sintético do Escriturador',
    'IPE_1_3_73': 'Assembleia | AGO/E | Mapa Sintético dos votos enviados diretamente à Companhia',
    'IPE_1_3_41': 'Assembleia | AGO/E | Material referente a pedidos públicos de procuração',
    'IPE_1_3_2': 'Assembleia | AGO/E | Proposta da Administração',
    'IPE_1_3_5': 'Assembleia | AGO/E | Protocolo de Incorporação, Cisão ou Fusão',
    'IPE_1_3_55': 'Assembleia | AGO/E | Protocolo e Justificação de Incorporação, Fusão ou Cisão',
    'IPE_1_3_3': 'Assembleia | AGO/E | Sumário das Decisões',
    'IPE_56_-1_-1': 'Ato Homologatório emitido pelo Banco Central | TODOS | TODAS',
    'IPE_3_-1_-1': 'Aviso aos Acionistas | TODOS | TODAS',
    'IPE_3_103_-1': 'Aviso aos Acionistas | Adoção do processo de voto múltiplo | TODAS',
    'IPE_3_104_-1': 'Aviso aos Acionistas | Adoção do voto a distância | TODAS',
    'IPE_3_96_-1':
        'Aviso aos Acionistas | Aumento de capital por subscrição privada deliberado em RCA | TODAS',
    'IPE_3_83_-1': 'Aviso aos Acionistas | Comunicado art.133 da Lei nº6.404/76 | TODAS',
    'IPE_3_105_-1': 'Aviso aos Acionistas | Data prevista para a assembleia | TODAS',
    'IPE_3_84_-1': 'Aviso aos Acionistas | Outros avisos | TODAS',
    'IPE_3_106_-1':
        'Aviso aos Acionistas | Solicitação de acionista para o Boletim de Voto | TODAS',
    'IPE_48_-1_-1': 'Aviso aos Debenturistas | TODOS | TODAS',
    'IPE_45_-1_-1': 'Balanço Social | TODOS | TODAS',
    'IPE_73_-1_-1': 'BDR Não Patrocinado - Descritivo Operacional do Programa | TODOS | TODAS',
    'IPE_9_-1_-1': 'Calendário de Eventos Corporativos | TODOS | TODAS',
    'IPE_91_-1_-1': 'Carta Anual de Governança Corporativa | TODOS | TODAS',
    'IPE_91_113_-1':
        'Carta Anual de Governança Corporativa | Carta Anual de Governança Corporativa (art. 8º, VIII da Lei 13.303/16 | TODAS',
    'IPE_91_114_-1':
        'Carta Anual de Governança Corporativa | Carta Anual de Políticas Públicas (art. 8º, I da Lei 13.303/16) | TODAS',
    'IPE_91_115_-1':
        'Carta Anual de Governança Corporativa | Carta consolidada de políticas públicas e governança corporativa (Decreto Federal 8.945/16) | TODAS',
    'IPE_74_-1_-1': 'Código de Conduta | TODOS | TODAS',
    'IPE_106_-1_-1': 'Comunicação sobre demandas societárias | TODOS | TODAS',
    'IPE_80_-1_-1': 'Comunicação sobre Transação entre Partes Relacionadas | TODOS | TODAS',
    'IPE_6_-1_-1': 'Comunicado ao Mercado | TODOS | TODAS',
    'IPE_6_-1_30':
        'Comunicado ao Mercado | TODOS | Declaração de Alienação de Participação Acionária Relevante',
    'IPE_6_-1_29':
        'Comunicado ao Mercado | TODOS | Declaração de Aquisição de Participação Acionária Relevante',
    'IPE_6_87_-1': 'Comunicado ao Mercado | Apresentações a analistas/agentes do mercado | TODAS',
    'IPE_6_51_-1':
        'Comunicado ao Mercado | Aquisição/Alienação de Participação Acionária Relevante | TODAS',
    'IPE_6_51_30':
        'Comunicado ao Mercado | Aquisição/Alienação de Participação Acionária Relevante | Declaração de Alienação de Participação Acionária Relevante',
    'IPE_6_51_29':
        'Comunicado ao Mercado | Aquisição/Alienação de Participação Acionária Relevante | Declaração de Aquisição de Participação Acionária Relevante',
    'IPE_6_52_-1': 'Comunicado ao Mercado | Esclarecimentos Sobre Consultas CVM/BOVESPA | TODAS',
    'IPE_6_134_-1': 'Comunicado ao Mercado | Esclarecimentos sobre questionamentos da B3 | TODAS',
    'IPE_6_112_-1':
        'Comunicado ao Mercado | Esclarecimentos sobre questionamentos da CVM/B3 | TODAS',
    'IPE_6_102_-1':
        'Comunicado ao Mercado | Instalação, mudança ou dissolução do Comitê de Auditoria Estatutário | TODAS',
    'IPE_6_55_-1': 'Comunicado ao Mercado | Mudança de Auditor | TODAS',
    'IPE_6_53_-1':
        'Comunicado ao Mercado | Outros Comunicados Não Considerados Fatos Relevantes | TODAS',
    'IPE_10_-1_-1': 'Contratos com Partes Relacionadas | TODOS | TODAS',
    'IPE_98_-1_-1': 'Contratos de Indenidade | TODOS | TODAS',
    'IPE_98_118_-1': 'Contratos de Indenidade | Contratos de indenidade e aditivos | TODAS',
    'IPE_98_119_-1':
        'Contratos de Indenidade | Outros documentos relacionados a contratos de indenidade | TODAS',
    'IPE_69_-1_-1': 'Convenção de grupos de sociedades | TODOS | TODAS',
    'IPE_84_-1_-1': 'Dados cadastrais de companhias incentivadas | TODOS | TODAS',
    'IPE_7_-1_-1': 'Dados Econômico-Financeiros | TODOS | TODAS',
    'IPE_7_-1_63': 'Dados Econômico-Financeiros | TODOS | Balanço Social',
    'IPE_7_-1_69': 'Dados Econômico-Financeiros | TODOS | Carve-out',
    'IPE_7_-1_67': 'Dados Econômico-Financeiros | TODOS | Combinadas',
    'IPE_7_-1_68': 'Dados Econômico-Financeiros | TODOS | Condensadas',
    'IPE_7_-1_36':
        'Dados Econômico-Financeiros | TODOS | Demonstrações Financeiras com reconciliação para IFRS',
    'IPE_7_-1_35':
        'Dados Econômico-Financeiros | TODOS | Demonstrações Financeiras com reconciliação para US GAAP',
    'IPE_7_-1_34': 'Dados Econômico-Financeiros | TODOS | Demonstrações Financeiras em IFRS',
    'IPE_7_-1_71':
        'Dados Econômico-Financeiros | TODOS | Demonstrações Financeiras em Padrões Internacionais',
    'IPE_7_-1_33': 'Dados Econômico-Financeiros | TODOS | Demonstrações Financeiras em US GAAP',
    'IPE_7_-1_42': 'Dados Econômico-Financeiros | TODOS | DFs Mercosul com reconciliação para IFRS',
    'IPE_7_-1_64':
        'Dados Econômico-Financeiros | TODOS | Especialmente preparadas para Registro Inicial',
    'IPE_7_-1_38': 'Dados Econômico-Financeiros | TODOS | Outros',
    'IPE_7_-1_65': 'Dados Econômico-Financeiros | TODOS | Pró-forma',
    'IPE_7_-1_70': 'Dados Econômico-Financeiros | TODOS | Regulatórias',
    'IPE_7_-1_66': 'Dados Econômico-Financeiros | TODOS | Separadas',
    'IPE_7_56_-1': 'Dados Econômico-Financeiros | Balanço Social | TODAS',
    'IPE_7_57_-1': 'Dados Econômico-Financeiros | Demonstração do Valor Adicionado | TODAS',
    'IPE_7_88_-1': 'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | TODAS',
    'IPE_7_88_63':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Balanço Social',
    'IPE_7_88_69': 'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Carve-out',
    'IPE_7_88_67':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Combinadas',
    'IPE_7_88_68':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Condensadas',
    'IPE_7_88_71':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Demonstrações Financeiras em Padrões Internacionais',
    'IPE_7_88_64':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Especialmente preparadas para Registro Inicial',
    'IPE_7_88_65': 'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Pró-forma',
    'IPE_7_88_70':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Regulatórias',
    'IPE_7_88_66': 'Dados Econômico-Financeiros | Demonstrações Financeiras Adicionais | Separadas',
    'IPE_7_37_-1':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Anuais Completas | TODAS',
    'IPE_7_42_-1':
        'Dados Econômico-Financeiros | Demonstrações Financeiras em Padrões Internacionais | TODAS',
    'IPE_7_42_36':
        'Dados Econômico-Financeiros | Demonstrações Financeiras em Padrões Internacionais | Demonstrações Financeiras com reconciliação para IFRS',
    'IPE_7_42_35':
        'Dados Econômico-Financeiros | Demonstrações Financeiras em Padrões Internacionais | Demonstrações Financeiras com reconciliação para US GAAP',
    'IPE_7_42_34':
        'Dados Econômico-Financeiros | Demonstrações Financeiras em Padrões Internacionais | Demonstrações Financeiras em IFRS',
    'IPE_7_42_33':
        'Dados Econômico-Financeiros | Demonstrações Financeiras em Padrões Internacionais | Demonstrações Financeiras em US GAAP',
    'IPE_7_42_42':
        'Dados Econômico-Financeiros | Demonstrações Financeiras em Padrões Internacionais | DFs Mercosul com reconciliação para IFRS',
    'IPE_7_42_38':
        'Dados Econômico-Financeiros | Demonstrações Financeiras em Padrões Internacionais | Outros',
    'IPE_7_45_-1':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Especiais - Registro Inicial de Cia Aberta | TODAS',
    'IPE_7_46_-1': 'Dados Econômico-Financeiros | Demonstrações Financeiras Intermediárias | TODAS',
    'IPE_7_46_42':
        'Dados Econômico-Financeiros | Demonstrações Financeiras Intermediárias | DFs Mercosul com reconciliação para IFRS',
    'IPE_7_20_-1': 'Dados Econômico-Financeiros | Laudo de Avaliação | TODAS',
    'IPE_7_63_-1':
        'Dados Econômico-Financeiros | Notificação do agente fiduciário aos debenturistas | TODAS',
    'IPE_7_85_-1': 'Dados Econômico-Financeiros | Plano de Investimento | TODAS',
    'IPE_7_29_-1': 'Dados Econômico-Financeiros | Press-release | TODAS',
    'IPE_7_58_-1': 'Dados Econômico-Financeiros | Relatório Anual | TODAS',
    'IPE_7_86_-1': 'Dados Econômico-Financeiros | Relatório de Agência de Rating | TODAS',
    'IPE_7_30_-1': 'Dados Econômico-Financeiros | Relatório de Agente Fiduciário | TODAS',
    'IPE_7_43_-1': 'Dados Econômico-Financeiros | Relatório de Análise Gerencial | TODAS',
    'IPE_111_-1_-1': 'DF - Padrão Internacional | TODOS | TODAS',
    'IPE_111_-1_71':
        'DF - Padrão Internacional | TODOS | Demonstrações Financeiras em Padrões Internacionais',
    'IPE_111_42_-1':
        'DF - Padrão Internacional | Demonstrações Financeiras em Padrões Internacionais | TODAS',
    'IPE_111_42_71':
        'DF - Padrão Internacional | Demonstrações Financeiras em Padrões Internacionais | Demonstrações Financeiras em Padrões Internacionais',
    'IPE_101_-1_-1': 'DF - Patrimônio separado | TODOS | TODAS',
    'IPE_78_-1_-1': 'Documentos de Oferta de Distribuição Pública | TODOS | TODAS',
    'IPE_78_-1_49':
        'Documentos de Oferta de Distribuição Pública | TODOS | Minuta de Prospecto Definitivo',
    'IPE_78_-1_48':
        'Documentos de Oferta de Distribuição Pública | TODOS | Minuta de Prospecto Preliminar',
    'IPE_78_-1_46': 'Documentos de Oferta de Distribuição Pública | TODOS | Prospecto Definitivo',
    'IPE_78_-1_45': 'Documentos de Oferta de Distribuição Pública | TODOS | Prospecto Preliminar',
    'IPE_78_-1_47':
        'Documentos de Oferta de Distribuição Pública | TODOS | Suplemento de Prospecto',
    'IPE_78_89_-1':
        'Documentos de Oferta de Distribuição Pública | Anúncio de Encerramento de Distribuição Pública | TODAS',
    'IPE_78_90_-1':
        'Documentos de Oferta de Distribuição Pública | Anúncio de Início de Distribuição Pública | TODAS',
    'IPE_78_91_-1': 'Documentos de Oferta de Distribuição Pública | Aviso ao Mercado | TODAS',
    'IPE_78_92_-1':
        'Documentos de Oferta de Distribuição Pública | Comunicação de Modificação de Oferta | TODAS',
    'IPE_78_101_-1':
        'Documentos de Oferta de Distribuição Pública | Comunicado de encerramento de oferta com esforços restritos | TODAS',
    'IPE_78_100_-1':
        'Documentos de Oferta de Distribuição Pública | Comunicado de início de oferta com esforços restritos | TODAS',
    'IPE_78_93_-1':
        'Documentos de Oferta de Distribuição Pública | Edital de Leilão de Ações | TODAS',
    'IPE_78_94_-1':
        'Documentos de Oferta de Distribuição Pública | Informações sobre o Programa de Distribuição Contínua | TODAS',
    'IPE_78_140_-1':
        'Documentos de Oferta de Distribuição Pública | Início e Encerramento de Oferta Direta realizada no FÁCIL | TODAS',
    'IPE_78_136_-1':
        'Documentos de Oferta de Distribuição Pública | Lâmina de Oferta de Ações/Units | TODAS',
    'IPE_78_137_-1':
        'Documentos de Oferta de Distribuição Pública | Lâmina de Oferta de Dívida | TODAS',
    'IPE_78_95_-1':
        'Documentos de Oferta de Distribuição Pública | Prospecto de Distribuição Pública | TODAS',
    'IPE_78_95_49':
        'Documentos de Oferta de Distribuição Pública | Prospecto de Distribuição Pública | Minuta de Prospecto Definitivo',
    'IPE_78_95_48':
        'Documentos de Oferta de Distribuição Pública | Prospecto de Distribuição Pública | Minuta de Prospecto Preliminar',
    'IPE_78_95_46':
        'Documentos de Oferta de Distribuição Pública | Prospecto de Distribuição Pública | Prospecto Definitivo',
    'IPE_78_95_45':
        'Documentos de Oferta de Distribuição Pública | Prospecto de Distribuição Pública | Prospecto Preliminar',
    'IPE_78_95_47':
        'Documentos de Oferta de Distribuição Pública | Prospecto de Distribuição Pública | Suplemento de Prospecto',
    'IPE_104_-1_-1': 'Documentos para análise da oferta pela B3 | TODOS | TODAS',
    'IPE_104_120_-1':
        'Documentos para análise da oferta pela B3 | Comprovante de pagamento de taxa (B3) | TODAS',
    'IPE_104_131_-1':
        'Documentos para análise da oferta pela B3 | Comunicados e Declarações (B3) | TODAS',
    'IPE_104_132_-1':
        'Documentos para análise da oferta pela B3 | Contratos de distribuição, estabilização e outros (B3) | TODAS',
    'IPE_104_130_-1':
        'Documentos para análise da oferta pela B3 | Documento com marcas de revisão (B3) | TODAS',
    'IPE_104_123_-1': 'Documentos para análise da oferta pela B3 | Requerimento à B3 | TODAS',
    'IPE_102_-1_-1': 'Documentos para listagem de companhia na B3 | TODOS | TODAS',
    'IPE_102_-1_38': 'Documentos para listagem de companhia na B3 | TODOS | Outros',
    'IPE_102_120_-1':
        'Documentos para listagem de companhia na B3 | Comprovante de pagamento de taxa (B3) | TODAS',
    'IPE_102_130_-1':
        'Documentos para listagem de companhia na B3 | Documento com marcas de revisão (B3) | TODAS',
    'IPE_102_130_38':
        'Documentos para listagem de companhia na B3 | Documento com marcas de revisão (B3) | Outros',
    'IPE_102_121_-1':
        'Documentos para listagem de companhia na B3 | Outros documentos (B3) | TODAS',
    'IPE_102_123_-1': 'Documentos para listagem de companhia na B3 | Requerimento à B3 | TODAS',
    'IPE_85_-1_-1': 'Documentos para registro de companhia | TODOS | TODAS',
    'IPE_85_108_-1':
        'Documentos para registro de companhia | Comprovante de pagamento de taxa | TODAS',
    'IPE_85_109_-1':
        'Documentos para registro de companhia | Documento com marcas de revisão | TODAS',
    'IPE_85_49_-1': 'Documentos para registro de companhia | Outros documentos | TODAS',
    'IPE_85_107_-1':
        'Documentos para registro de companhia | Requerimento à CVM e/ou à BM&FBOVESPA | TODAS',
    'IPE_103_-1_-1': 'Documentos para registro de companhia na CVM | TODOS | TODAS',
    'IPE_103_-1_60':
        'Documentos para registro de companhia na CVM | TODOS | Termo de Posse de Membro da Diretoria',
    'IPE_103_127_-1':
        'Documentos para registro de companhia na CVM | Documento com marcas de revisão - DFP | TODAS',
    'IPE_103_129_-1':
        'Documentos para registro de companhia na CVM | Documento com marcas de revisão - Estatuto Social | TODAS',
    'IPE_103_126_-1':
        'Documentos para registro de companhia na CVM | Documento com marcas de revisão - Formulário Cadastral | TODAS',
    'IPE_103_125_-1':
        'Documentos para registro de companhia na CVM | Documento com marcas de revisão - Formulário de Referência | TODAS',
    'IPE_103_128_-1':
        'Documentos para registro de companhia na CVM | Documento com marcas de revisão - ITR | TODAS',
    'IPE_103_122_-1':
        'Documentos para registro de companhia na CVM | Outros documentos (CVM) | TODAS',
    'IPE_103_124_-1': 'Documentos para registro de companhia na CVM | Requerimento à CVM | TODAS',
    'IPE_103_135_-1': 'Documentos para registro de companhia na CVM | Termo de Posse | TODAS',
    'IPE_103_135_60':
        'Documentos para registro de companhia na CVM | Termo de Posse | Termo de Posse de Membro da Diretoria',
    'IPE_86_-1_-1': 'Documentos para registro de oferta | TODOS | TODAS',
    'IPE_86_108_-1':
        'Documentos para registro de oferta | Comprovante de pagamento de taxa | TODAS',
    'IPE_86_110_-1': 'Documentos para registro de oferta | Comunicados e Declarações | TODAS',
    'IPE_86_111_-1':
        'Documentos para registro de oferta | Contratos de distribuição, estabilização e outros | TODAS',
    'IPE_86_109_-1': 'Documentos para registro de oferta | Documento com marcas de revisão | TODAS',
    'IPE_86_49_-1': 'Documentos para registro de oferta | Outros documentos | TODAS',
    'IPE_86_107_-1':
        'Documentos para registro de oferta | Requerimento à CVM e/ou à BM&FBOVESPA | TODAS',
    'IPE_109_-1_-1': 'Emissor Estrangeiro (entidade de investimentos) | TODOS | TODAS',
    'IPE_109_138_-1':
        'Emissor Estrangeiro (entidade de investimentos) | Composição de carteira | TODAS',
    'IPE_109_139_-1':
        'Emissor Estrangeiro (entidade de investimentos) | Demais informações específicas obrigatórias | TODAS',
    'IPE_54_-1_-1': 'Escrituras e aditamentos de debêntures | TODOS | TODAS',
    'IPE_70_-1_-1': 'Estatuto Social | TODOS | TODAS',
    'IPE_4_-1_-1': 'Fato Relevante | TODOS | TODAS',
    'IPE_66_-1_-1': 'Formulário Cadastral - Em arquivo | TODOS | TODAS',
    'IPE_112_-1_-1': 'Formulário de Informações Semestrais - ISEM | TODOS | TODAS',
    'IPE_67_-1_-1': 'Formulário de Referência - Em arquivo | TODOS | TODAS',
    'IPE_67_61_-1':
        'Formulário de Referência - Em arquivo | Atualizações previstas no art. 24 da IN480/09 | TODAS',
    'IPE_67_60_-1': 'Formulário de Referência - Em arquivo | Formulário Completo | TODAS',
    'IPE_113_-1_-1': 'Formulário FÁCIL | TODOS | TODAS',
    'IPE_5_-1_-1': 'Informação Prestada às Bolsas Estrangeiras | TODOS | TODAS',
    'IPE_46_-1_-1': 'Informações Companhias em Falência | TODOS | TODAS',
    'IPE_46_68_-1':
        'Informações Companhias em Falência | Causas e circunstâncias da falência | TODAS',
    'IPE_46_71_-1':
        'Informações Companhias em Falência | Contas apresentadas ao final do processo de falência | TODAS',
    'IPE_46_69_-1':
        'Informações Companhias em Falência | Contas demonstrativas da administração | TODAS',
    'IPE_46_70_-1': 'Informações Companhias em Falência | Outras informações contábeis | TODAS',
    'IPE_46_72_-1': 'Informações Companhias em Falência | Relatório final | TODAS',
    'IPE_46_73_-1': 'Informações Companhias em Falência | Sentença de encerramento | TODAS',
    'IPE_47_-1_-1': 'Informações Companhias em Liquidação | TODOS | TODAS',
    'IPE_47_81_-1':
        'Informações Companhias em Liquidação | Ato de encerramento da liquidação | TODAS',
    'IPE_47_75_-1': 'Informações Companhias em Liquidação | Destituição de Liquidante | TODAS',
    'IPE_47_74_-1': 'Informações Companhias em Liquidação | Nomeação de Liquidante | TODAS',
    'IPE_47_80_-1':
        'Informações Companhias em Liquidação | Outros relatórios, pareceres ou informações contábeis | TODAS',
    'IPE_47_77_-1': 'Informações Companhias em Liquidação | Quadro geral de credores | TODAS',
    'IPE_47_78_-1':
        'Informações Companhias em Liquidação | Quadro geral de credores definitivo | TODAS',
    'IPE_47_79_-1':
        'Informações Companhias em Liquidação | Relatório e balanço final da liquidação | TODAS',
    'IPE_47_76_-1': 'Informações Companhias em Liquidação | Substituição de Liquidante | TODAS',
    'IPE_58_-1_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | TODOS | TODAS',
    'IPE_58_-1_4':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | TODOS | Ata',
    'IPE_58_-1_28':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | TODOS | Edital de Convocação',
    'IPE_58_-1_32':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | TODOS | Proposta para a assembleia',
    'IPE_58_-1_3':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | TODOS | Sumário das Decisões',
    'IPE_58_54_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Assembleia de Credores | TODAS',
    'IPE_58_54_4':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Assembleia de Credores | Ata',
    'IPE_58_54_28':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Assembleia de Credores | Edital de Convocação',
    'IPE_58_54_32':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Assembleia de Credores | Proposta para a assembleia',
    'IPE_58_54_3':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Assembleia de Credores | Sumário das Decisões',
    'IPE_58_66_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Contas demonstrativas mensais | TODAS',
    'IPE_58_49_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Outros documentos | TODAS',
    'IPE_58_64_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Pedido de homologação de Plano de Recuperação Extrajudicial | TODAS',
    'IPE_58_47_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Petição Inicial | TODAS',
    'IPE_58_48_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Plano de Recuperação | TODAS',
    'IPE_58_67_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Relatório circunstanciado | TODAS',
    'IPE_58_65_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Relatório de cumprimento do plano | TODAS',
    'IPE_58_50_-1':
        'Informações de Companhias em Recuperação Judicial ou Extrajudicial | Sentenças | TODAS',
    'IPE_68_-1_-1': 'Informações sobre acordos de acionistas | TODOS | TODAS',
    'IPE_75_-1_-1':
        'Informações sobre o Programa de Distribuição Contínua (Anexo X da IN CVM n° 400/03) | TODOS | TODAS',
    'IPE_100_-1_-1': 'Informe Mensal de CRA | TODOS | TODAS',
    'IPE_22_-1_-1': 'OPA - Edital de Oferta Pública de Ações | TODOS | TODAS',
    'IPE_63_-1_-1':
        'Parecer Jurídico - BDRs Níveis II e III (Art.5º, inciso III, alínea d, Instr. CVM 331/00) | TODOS | TODAS',
    'IPE_71_-1_-1': 'Pedidos de falência | TODOS | TODAS',
    'IPE_53_-1_-1': 'Plano de Remuneração Baseado em Ações | TODOS | TODAS',
    'IPE_79_-1_-1':
        'Plano de Remuneração Baseado em Ações (exceto Plano de Opções) | TODOS | TODAS',
    'IPE_64_-1_-1':
        'Plano de Remuneração ou Retenção de Administradores e Executivos | TODOS | TODAS',
    'IPE_94_-1_-1': 'Política de Destinação de Resultados | TODOS | TODAS',
    'IPE_83_-1_-1': 'Política de Dividendos | TODOS | TODAS',
    'IPE_11_-1_-1': 'Política de Divulgação de Ato ou Fato Relevante | TODOS | TODAS',
    'IPE_82_-1_-1': 'Política de Gerenciamento de Riscos | TODOS | TODAS',
    'IPE_87_-1_-1': 'Política de indicação | TODOS | TODAS',
    'IPE_12_-1_-1': 'Política de Negociação das Ações da Companhia | TODOS | TODAS',
    'IPE_95_-1_-1': 'Política de Negociação de Valores Mobiliários | TODOS | TODAS',
    'IPE_92_-1_-1': 'Política de Remuneração | TODOS | TODAS',
    'IPE_105_-1_-1': 'Política de Sustentabilidade | TODOS | TODAS',
    'IPE_81_-1_-1': 'Política de Transações entre Partes Relacionadas | TODOS | TODAS',
    'IPE_93_-1_-1':
        'Política para Contratação de Serviços Extra-Auditoria de seus Auditores Independentes | TODOS | TODAS',
    'IPE_96_-1_-1': 'Política sobre Contribuições e Doações | TODOS | TODAS',
    'IPE_21_-1_-1': 'Prospecto de Distribuição Pública | TODOS | TODAS',
    'IPE_97_-1_-1': 'Regimento Interno da Diretoria | TODOS | TODAS',
    'IPE_90_-1_-1': 'Regimento interno de Comitês | TODOS | TODAS',
    'IPE_76_-1_-1': 'Regimento Interno do Comitê de Auditoria Estatutário | TODOS | TODAS',
    'IPE_88_-1_-1': 'Regimento interno do Conselho de Administração | TODOS | TODAS',
    'IPE_89_-1_-1': 'Regimento interno do Conselho Fiscal | TODOS | TODAS',
    'IPE_99_-1_-1': 'Regulamentos da B3 | TODOS | TODAS',
    'IPE_99_-1_58': 'Regulamentos da B3 | TODOS | Defesa - Notificação B3',
    'IPE_99_-1_59': 'Regulamentos da B3 | TODOS | Recurso',
    'IPE_99_117_-1': 'Regulamentos da B3 | Processo de enforcement | TODAS',
    'IPE_99_117_58': 'Regulamentos da B3 | Processo de enforcement | Defesa - Notificação B3',
    'IPE_99_117_59': 'Regulamentos da B3 | Processo de enforcement | Recurso',
    'IPE_114_-1_-1': 'Relação de Dispensa de Obrigações Regulatórias | TODOS | TODAS',
    'IPE_108_-1_-1': 'Relato Integrado | TODOS | TODAS',
    'IPE_110_-1_-1':
        'Relatório de Informações Financeiras Relacionadas à Sustentabilidade - Padrão ISSB | TODOS | TODAS',
    'IPE_77_-1_-1': 'Relatório de Sustentabilidade | TODOS | TODAS',
    'IPE_107_-1_-1': 'Relatório Proventos | TODOS | TODAS',
    'IPE_2_-1_-1': 'Reunião da Administração | TODOS | TODAS',
    'IPE_2_-1_4': 'Reunião da Administração | TODOS | Ata',
    'IPE_2_-1_3': 'Reunião da Administração | TODOS | Sumário das Decisões',
    'IPE_2_82_-1': 'Reunião da Administração | Comitê de Auditoria | TODAS',
    'IPE_2_82_4': 'Reunião da Administração | Comitê de Auditoria | Ata',
    'IPE_2_32_-1': 'Reunião da Administração | Conselho Consultivo | TODAS',
    'IPE_2_32_4': 'Reunião da Administração | Conselho Consultivo | Ata',
    'IPE_2_32_3': 'Reunião da Administração | Conselho Consultivo | Sumário das Decisões',
    'IPE_2_31_-1': 'Reunião da Administração | Conselho de Administração | TODAS',
    'IPE_2_31_4': 'Reunião da Administração | Conselho de Administração | Ata',
    'IPE_2_31_3': 'Reunião da Administração | Conselho de Administração | Sumário das Decisões',
    'IPE_2_33_-1': 'Reunião da Administração | Conselho Fiscal | TODAS',
    'IPE_2_33_4': 'Reunião da Administração | Conselho Fiscal | Ata',
    'IPE_2_33_3': 'Reunião da Administração | Conselho Fiscal | Sumário das Decisões',
    'IPE_2_34_-1': 'Reunião da Administração | Diretoria | TODAS',
    'IPE_2_34_4': 'Reunião da Administração | Diretoria | Ata',
    'IPE_2_34_3': 'Reunião da Administração | Diretoria | Sumário das Decisões',
    'IPE_57_-1_-1': 'Sentença de Falência | TODOS | TODAS',
    'IPE_116_-1_-1': 'Termo de Emissão de Nota Comercial | TODOS | TODAS',
    'IPE_72_-1_-1': 'Termo de Securitização e Aditamentos de Direitos Creditórios | TODOS | TODAS',
    'IPE_8_-1_-1': 'Valores Mobiliários Negociados e Detidos | TODOS | TODAS',
    'IPE_8_38_-1':
        'Valores Mobiliários Negociados e Detidos | Empresas Nível 1, 2 ou Novo Mercado - Posição Individual | TODAS',
    'IPE_8_7_-1': 'Valores Mobiliários Negociados e Detidos | Posição Consolidada | TODAS',
    'IPE_8_6_-1': 'Valores Mobiliários Negociados e Detidos | Posição Individual | TODAS',
    'IPE_8_99_-1':
        'Valores Mobiliários Negociados e Detidos | Posição Individual - Cia, Controladas e Coligadas | TODAS',
    'IPE_8_44_-1':
        'Valores Mobiliários Negociados e Detidos | Posse de Administrador (Valores Mobiliários Detidos na Posse) | TODAS',
    'IPE_-1_98_-1': 'Tipo: AGCRA',
    'IPE_-1_97_-1': 'Tipo: AGCRI',
    'IPE_-1_5_-1': 'Tipo: AGDEB',
    'IPE_-1_1_-1': 'Tipo: AGE',
    'IPE_-1_4_-1': 'Tipo: AGESP',
    'IPE_-1_2_-1': 'Tipo: AGO',
    'IPE_-1_3_-1': 'Tipo: AGO/E',
    'IPE_-1_103_-1': 'Tipo: Adoção do processo de voto múltiplo',
    'IPE_-1_104_-1': 'Tipo: Adoção do voto a distância',
    'IPE_-1_96_-1': 'Tipo: Aumento de capital por subscrição privada deliberado em RCA',
    'IPE_-1_83_-1': 'Tipo: Comunicado art.133 da Lei nº6.404/76',
    'IPE_-1_105_-1': 'Tipo: Data prevista para a assembleia',
    'IPE_-1_84_-1': 'Tipo: Outros avisos',
    'IPE_-1_106_-1': 'Tipo: Solicitação de acionista para o Boletim de Voto',
    'IPE_-1_113_-1': 'Tipo: Carta Anual de Governança Corporativa (art. 8º, VIII da Lei 13.303/16',
    'IPE_-1_114_-1': 'Tipo: Carta Anual de Políticas Públicas (art. 8º, I da Lei 13.303/16)',
    'IPE_-1_115_-1':
        'Tipo: Carta consolidada de políticas públicas e governança corporativa (Decreto Federal 8.945/16)',
    'IPE_-1_87_-1': 'Tipo: Apresentações a analistas/agentes do mercado',
    'IPE_-1_51_-1': 'Tipo: Aquisição/Alienação de Participação Acionária Relevante',
    'IPE_-1_52_-1': 'Tipo: Esclarecimentos Sobre Consultas CVM/BOVESPA',
    'IPE_-1_134_-1': 'Tipo: Esclarecimentos sobre questionamentos da B3',
    'IPE_-1_112_-1': 'Tipo: Esclarecimentos sobre questionamentos da CVM/B3',
    'IPE_-1_102_-1': 'Tipo: Instalação, mudança ou dissolução do Comitê de Auditoria Estatutário',
    'IPE_-1_55_-1': 'Tipo: Mudança de Auditor',
    'IPE_-1_53_-1': 'Tipo: Outros Comunicados Não Considerados Fatos Relevantes',
    'IPE_-1_118_-1': 'Tipo: Contratos de indenidade e aditivos',
    'IPE_-1_119_-1': 'Tipo: Outros documentos relacionados a contratos de indenidade',
    'IPE_-1_56_-1': 'Tipo: Balanço Social',
    'IPE_-1_57_-1': 'Tipo: Demonstração do Valor Adicionado',
    'IPE_-1_88_-1': 'Tipo: Demonstrações Financeiras Adicionais',
    'IPE_-1_37_-1': 'Tipo: Demonstrações Financeiras Anuais Completas',
    'IPE_-1_42_-1': 'Tipo: Demonstrações Financeiras em Padrões Internacionais',
    'IPE_-1_45_-1': 'Tipo: Demonstrações Financeiras Especiais - Registro Inicial de Cia Aberta',
    'IPE_-1_46_-1': 'Tipo: Demonstrações Financeiras Intermediárias',
    'IPE_-1_20_-1': 'Tipo: Laudo de Avaliação',
    'IPE_-1_63_-1': 'Tipo: Notificação do agente fiduciário aos debenturistas',
    'IPE_-1_85_-1': 'Tipo: Plano de Investimento',
    'IPE_-1_29_-1': 'Tipo: Press-release',
    'IPE_-1_58_-1': 'Tipo: Relatório Anual',
    'IPE_-1_86_-1': 'Tipo: Relatório de Agência de Rating',
    'IPE_-1_30_-1': 'Tipo: Relatório de Agente Fiduciário',
    'IPE_-1_43_-1': 'Tipo: Relatório de Análise Gerencial',
    'IPE_-1_89_-1': 'Tipo: Anúncio de Encerramento de Distribuição Pública',
    'IPE_-1_90_-1': 'Tipo: Anúncio de Início de Distribuição Pública',
    'IPE_-1_91_-1': 'Tipo: Aviso ao Mercado',
    'IPE_-1_92_-1': 'Tipo: Comunicação de Modificação de Oferta',
    'IPE_-1_101_-1': 'Tipo: Comunicado de encerramento de oferta com esforços restritos',
    'IPE_-1_100_-1': 'Tipo: Comunicado de início de oferta com esforços restritos',
    'IPE_-1_93_-1': 'Tipo: Edital de Leilão de Ações',
    'IPE_-1_94_-1': 'Tipo: Informações sobre o Programa de Distribuição Contínua',
    'IPE_-1_140_-1': 'Tipo: Início e Encerramento de Oferta Direta realizada no FÁCIL',
    'IPE_-1_136_-1': 'Tipo: Lâmina de Oferta de Ações/Units',
    'IPE_-1_137_-1': 'Tipo: Lâmina de Oferta de Dívida',
    'IPE_-1_95_-1': 'Tipo: Prospecto de Distribuição Pública',
    'IPE_-1_120_-1': 'Tipo: Comprovante de pagamento de taxa (B3)',
    'IPE_-1_131_-1': 'Tipo: Comunicados e Declarações (B3)',
    'IPE_-1_132_-1': 'Tipo: Contratos de distribuição, estabilização e outros (B3)',
    'IPE_-1_130_-1': 'Tipo: Documento com marcas de revisão (B3)',
    'IPE_-1_123_-1': 'Tipo: Requerimento à B3',
    'IPE_-1_121_-1': 'Tipo: Outros documentos (B3)',
    'IPE_-1_108_-1': 'Tipo: Comprovante de pagamento de taxa',
    'IPE_-1_109_-1': 'Tipo: Documento com marcas de revisão',
    'IPE_-1_49_-1': 'Tipo: Outros documentos',
    'IPE_-1_107_-1': 'Tipo: Requerimento à CVM e/ou à BM&FBOVESPA',
    'IPE_-1_127_-1': 'Tipo: Documento com marcas de revisão - DFP',
    'IPE_-1_129_-1': 'Tipo: Documento com marcas de revisão - Estatuto Social',
    'IPE_-1_126_-1': 'Tipo: Documento com marcas de revisão - Formulário Cadastral',
    'IPE_-1_125_-1': 'Tipo: Documento com marcas de revisão - Formulário de Referência',
    'IPE_-1_128_-1': 'Tipo: Documento com marcas de revisão - ITR',
    'IPE_-1_122_-1': 'Tipo: Outros documentos (CVM)',
    'IPE_-1_124_-1': 'Tipo: Requerimento à CVM',
    'IPE_-1_135_-1': 'Tipo: Termo de Posse',
    'IPE_-1_110_-1': 'Tipo: Comunicados e Declarações',
    'IPE_-1_111_-1': 'Tipo: Contratos de distribuição, estabilização e outros',
    'IPE_-1_138_-1': 'Tipo: Composição de carteira',
    'IPE_-1_139_-1': 'Tipo: Demais informações específicas obrigatórias',
    'IPE_-1_61_-1': 'Tipo: Atualizações previstas no art. 24 da IN480/09',
    'IPE_-1_60_-1': 'Tipo: Formulário Completo',
    'IPE_-1_68_-1': 'Tipo: Causas e circunstâncias da falência',
    'IPE_-1_71_-1': 'Tipo: Contas apresentadas ao final do processo de falência',
    'IPE_-1_69_-1': 'Tipo: Contas demonstrativas da administração',
    'IPE_-1_70_-1': 'Tipo: Outras informações contábeis',
    'IPE_-1_72_-1': 'Tipo: Relatório final',
    'IPE_-1_73_-1': 'Tipo: Sentença de encerramento',
    'IPE_-1_81_-1': 'Tipo: Ato de encerramento da liquidação',
    'IPE_-1_75_-1': 'Tipo: Destituição de Liquidante',
    'IPE_-1_74_-1': 'Tipo: Nomeação de Liquidante',
    'IPE_-1_80_-1': 'Tipo: Outros relatórios, pareceres ou informações contábeis',
    'IPE_-1_77_-1': 'Tipo: Quadro geral de credores',
    'IPE_-1_78_-1': 'Tipo: Quadro geral de credores definitivo',
    'IPE_-1_79_-1': 'Tipo: Relatório e balanço final da liquidação',
    'IPE_-1_76_-1': 'Tipo: Substituição de Liquidante',
    'IPE_-1_54_-1': 'Tipo: Assembleia de Credores',
    'IPE_-1_66_-1': 'Tipo: Contas demonstrativas mensais',
    'IPE_-1_64_-1': 'Tipo: Pedido de homologação de Plano de Recuperação Extrajudicial',
    'IPE_-1_47_-1': 'Tipo: Petição Inicial',
    'IPE_-1_48_-1': 'Tipo: Plano de Recuperação',
    'IPE_-1_67_-1': 'Tipo: Relatório circunstanciado',
    'IPE_-1_65_-1': 'Tipo: Relatório de cumprimento do plano',
    'IPE_-1_50_-1': 'Tipo: Sentenças',
    'IPE_-1_117_-1': 'Tipo: Processo de enforcement',
    'IPE_-1_82_-1': 'Tipo: Comitê de Auditoria',
    'IPE_-1_32_-1': 'Tipo: Conselho Consultivo',
    'IPE_-1_31_-1': 'Tipo: Conselho de Administração',
    'IPE_-1_33_-1': 'Tipo: Conselho Fiscal',
    'IPE_-1_34_-1': 'Tipo: Diretoria',
    'IPE_-1_38_-1': 'Tipo: Empresas Nível 1, 2 ou Novo Mercado - Posição Individual',
    'IPE_-1_7_-1': 'Tipo: Posição Consolidada',
    'IPE_-1_6_-1': 'Tipo: Posição Individual',
    'IPE_-1_99_-1': 'Tipo: Posição Individual - Cia, Controladas e Coligadas',
    'IPE_-1_44_-1': 'Tipo: Posse de Administrador (Valores Mobiliários Detidos na Posse)',
    'IPE_-1_-1_4': 'Espécie: Ata',
    'IPE_-1_-1_28': 'Espécie: Edital de Convocação',
    'IPE_-1_-1_32': 'Espécie: Proposta para a assembleia',
    'IPE_-1_-1_3': 'Espécie: Sumário das Decisões',
    'IPE_-1_-1_61': 'Espécie: Instrução de voto',
    'IPE_-1_-1_44': 'Espécie: Manual para participação',
    'IPE_-1_-1_2': 'Espécie: Proposta da Administração',
    'IPE_-1_-1_54': 'Espécie: Boletim de voto a distância',
    'IPE_-1_-1_6': 'Espécie: Justificação de Incorporação, Cisão ou Fusão',
    'IPE_-1_-1_53': 'Espécie: Mapa final de votação',
    'IPE_-1_-1_56': 'Espécie: Mapa Final de Votação Detalhado',
    'IPE_-1_-1_72': 'Espécie: Mapa Final de Votação Resumido',
    'IPE_-1_-1_57': 'Espécie: Mapa Final de Votação Sintético',
    'IPE_-1_-1_52': 'Espécie: Mapa Sintético Consolidado',
    'IPE_-1_-1_51': 'Espécie: Mapa Sintético do Depositário Central',
    'IPE_-1_-1_50': 'Espécie: Mapa Sintético do Escriturador',
    'IPE_-1_-1_73': 'Espécie: Mapa Sintético dos votos enviados diretamente à Companhia',
    'IPE_-1_-1_41': 'Espécie: Material referente a pedidos públicos de procuração',
    'IPE_-1_-1_5': 'Espécie: Protocolo de Incorporação, Cisão ou Fusão',
    'IPE_-1_-1_55': 'Espécie: Protocolo e Justificação de Incorporação, Fusão ou Cisão',
    'IPE_-1_-1_30': 'Espécie: Declaração de Alienação de Participação Acionária Relevante',
    'IPE_-1_-1_29': 'Espécie: Declaração de Aquisição de Participação Acionária Relevante',
    'IPE_-1_-1_63': 'Espécie: Balanço Social',
    'IPE_-1_-1_69': 'Espécie: Carve-out',
    'IPE_-1_-1_67': 'Espécie: Combinadas',
    'IPE_-1_-1_68': 'Espécie: Condensadas',
    'IPE_-1_-1_71': 'Espécie: Demonstrações Financeiras em Padrões Internacionais',
    'IPE_-1_-1_64': 'Espécie: Especialmente preparadas para Registro Inicial',
    'IPE_-1_-1_65': 'Espécie: Pró-forma',
    'IPE_-1_-1_70': 'Espécie: Regulatórias',
    'IPE_-1_-1_66': 'Espécie: Separadas',
    'IPE_-1_-1_36': 'Espécie: Demonstrações Financeiras com reconciliação para IFRS',
    'IPE_-1_-1_35': 'Espécie: Demonstrações Financeiras com reconciliação para US GAAP',
    'IPE_-1_-1_34': 'Espécie: Demonstrações Financeiras em IFRS',
    'IPE_-1_-1_33': 'Espécie: Demonstrações Financeiras em US GAAP',
    'IPE_-1_-1_42': 'Espécie: DFs Mercosul com reconciliação para IFRS',
    'IPE_-1_-1_38': 'Espécie: Outros',
    'IPE_-1_-1_49': 'Espécie: Minuta de Prospecto Definitivo',
    'IPE_-1_-1_48': 'Espécie: Minuta de Prospecto Preliminar',
    'IPE_-1_-1_46': 'Espécie: Prospecto Definitivo',
    'IPE_-1_-1_45': 'Espécie: Prospecto Preliminar',
    'IPE_-1_-1_47': 'Espécie: Suplemento de Prospecto',
    'IPE_-1_-1_60': 'Espécie: Termo de Posse de Membro da Diretoria',
    'IPE_-1_-1_58': 'Espécie: Defesa - Notificação B3',
    'IPE_-1_-1_59': 'Espécie: Recurso',
})
