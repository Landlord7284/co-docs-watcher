# A fonte: RAD/ENETWeb

Contrato levantado por inspeção do JavaScript da página e por requisições reais
contra o ambiente de produção da CVM em **24/08/2026**. Não existe contrato de
API: isto é raspagem de um endpoint de UI. Re-verificar, não confiar
indefinidamente.

O sistema entrou no ar em **06/07/2026**, substituindo o `ENETCONSULTA`. Receitas
publicadas antes disso apontam para o endpoint antigo.

- Página: `https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx`
- Stack: ASP.NET WebForms + jQuery UI + DataTables 1.10.11 + pdf.js + select2
- Fonte dos fatos abaixo: `js/forms/frmConsultaExternaCVM.js` e `js/common.js`

---

## 1. Busca

Um único PageMethod. **Sem `__VIEWSTATE`, sem cookie de sessão, sem passo
intermediário** — verificado com `credentials: 'omit'`: busca e download
respondem igual sem nenhum cookie.

```http
POST https://www.rad.cvm.gov.br/ENETWeb/frmConsultaExternaCVM.aspx/ListarDocumentos
Content-Type: application/json; charset=UTF-8

{
  "dataDe": "01/08/2026",
  "dataAte": "24/08/2026",
  "empresa": ",009512",
  "setorAtividade": "-1",
  "categoriaEmissor": "-1",
  "situacaoEmissor": "-1",
  "tipoParticipante": "-1",
  "dataReferencia": "",
  "categoria": "EST_-1,IPE_-1_-1_-1",
  "periodo": "2",
  "horaIni": "",
  "horaFim": "",
  "palavraChave": "",
  "ultimaDtRef": "false",
  "tipoEmpresa": "0",
  "token": "",
  "versaoCaptcha": ""
}
```

Esse payload exato devolveu 18 documentos da Petrobras.

### Parâmetros

| Campo | Valor | Observação |
|---|---|---|
| `empresa` | `,009512` | Lista separada por vírgula, **com vírgula inicial**. Código CVM zero-padded em 6. Vazio = todas as companhias. |
| `periodo` | `0` / `1` / `2` | No dia / Na semana / No período. Só com `2` as datas são lidas. |
| `dataDe`, `dataAte` | `dd/MM/yyyy` | Filtram por **data de entrega**, não por data de referência. Ambos inclusivos. |
| `categoria` | `EST_-1,IPE_-1_-1_-1` | Tudo. `EST_*` estruturados, `IPE_*_*_*` eventuais (categoria_tipo_espécie). |
| `tipoEmpresa` | `0` | É o que o front envia por padrão. `1` e `2` existem no código. |
| `dataReferencia` | `""` | Mutuamente exclusivo com `ultimaDtRef`. |
| `token`, `versaoCaptcha` | `""`, `""` | Vazios na primeira tentativa. Só preenchidos se o servidor pedir. |
| `setorAtividade`, `categoriaEmissor`, `situacaoEmissor`, `tipoParticipante` | `-1` | `-1` = todos. Valores reais saem dos `<select>` da própria página. |

### Armadilhas de formato

- A **vírgula inicial** em `empresa` é artefato do `join` do front, mas o
  servidor a espera.
- O código CVM vai com **seis dígitos e zeros à esquerda** no payload, e volta
  **formatado com hífen** (`00951-2`) no campo 0 da resposta.

---

## 2. Resposta

O envelope é JSON. O conteúdo, não: os documentos vêm como uma única string com
separadores literais, herança do sistema antigo.

```json
{"d": {
  "__type": "frmConsultaExternaCVM+RetornoTelaConsultaExterna",
  "temErro": false,
  "expirouSessao": false,
  "msgErro": "",
  "SolicitarCaptcha": "N",
  "dados": "linha $&&* linha $&&* linha ..."
}}
```

| | |
|---|---|
| Separador de linha | `$&&*` |
| Separador de campo | `$&` |

**O HTTP é sempre 200.** Erro de negócio e falha de backend chegam como
`temErro: true` com o texto em `msgErro`. Um robô que só olha o status code
grava silêncio como "nada novo".

`dados` **termina com o separador de linha**, então o último elemento do split é
vazio — descartar antes de contar.

### Os 12 campos de cada linha

| # | Campo | Exemplo real |
|---:|---|---|
| 0 | Código CVM formatado | `00951-2` |
| 1 | Razão social | `PETROLEO BRASILEIRO S.A. PETROBRAS` |
| 2 | Categoria | `Fato Relevante` |
| 3 | Tipo | `Outros Comunicados…` |
| 4 | Espécie | `<spanOrder></spanOrder> -` |
| 5 | Data de referência | `<spanOrder>20261231</spanOrder> 2026` |
| 6 | Data de entrega | `<spanOrder>20260804</spanOrder> 04/08/2026 15:37` |
| 7 | Status | `Ativo` / `Inativo` / `Cancelado` |
| 8 | Versão | `7` |
| 9 | Modalidade | `AP` / `RE` |
| 10 | HTML dos ícones de ação | contém os identificadores de download |
| 11 | **Assunto** | `Petrobras informa sobre remuneração aos acionistas` |

**Campo 11 é o assunto** — o texto que a interface mostra como `Assunto(s):`.
Preenchido nos documentos eventuais, vazio nos estruturados.

**Campos 4, 5 e 6 trazem chave de ordenação embutida** em `<spanOrder>`. É ruído
para renderizar e presente para parsear: `20260804` é a data já normalizada, sem
precisar interpretar `dd/MM/yyyy` nem os formatos alternativos de data de
referência.

---

## 3. Descoberta global

Com `empresa` vazio, a consulta devolve todas as companhias do período. **Sem
paginação, sem parâmetro de página, sem truncamento.**

| Consulta | Documentos | Companhias | Tempo |
|---|---:|---:|---:|
| Petrobras, 24 dias | 18 | 1 | — |
| Petrobras + Vale, 24 dias | 38 | 2 | — |
| Mercado inteiro, 21/08 | 479 | 327 | 3,3 s |
| Mercado inteiro, 17–21/08 | 2.235 | 852 | 6,3 s |

As contagens fecham: os 479 documentos de 21/08 aparecem idênticos na consulta do
dia isolado e dentro da janela de cinco dias.

**Isto inverte a decisão central do `fii-docs-watcher`.** Lá a listagem global é
proibida como caminho de descoberta, porque `cnpjFundo` e `idFundo` vêm nulos em
toda linha e rotear por texto é falha silenciosa. Aqui o código CVM vem no campo
0 de *toda* linha: o roteamento é exato, e uma requisição por dia atende uma
lista de vigilância de qualquer tamanho.

Volume de referência: **~450 documentos/dia** no mercado inteiro.

---

## 4. Download

O campo 10 carrega `OpenDownloadDocumentos(numSequencia, numVersao, numProtocolo, descTipo)`.
Os quatro argumentos montam uma URL GET direta:

```
GET frmDownloadDocumento.aspx
      ?Tela=ext
      &numSequencia=160125
      &numVersao=7
      &numProtocolo=009512FRE202620260700160125-70
      &descTipo=
      &CodigoInstituicao=1
```

**Um caminho só para todas as categorias** — diferente do Fundos.NET. O que muda
é o conteúdo.

| Categoria testada | Retorno | Bytes | Magic |
|---|---|---:|---|
| Fato Relevante | PDF | 114.287 | `%PDF-1.6` |
| ITR | ZIP | 8.648.457 | `PK…` |
| FRE | ZIP | 8.406.538 | `PK…` |

**O `Content-Type` mente.** Veio `text/html` nos três casos. O tipo real sai do
*magic number* ou do `Content-Disposition`, nunca do header de tipo.

O `Content-Disposition` **não serve para nomear**: o do fato relevante veio
`009512000101011.pdf` — código CVM + data de referência nula + versão. Sem id,
sem data legível. O nome no disco tem que ser construído pelo robô.

### Conteúdo do ZIP (ITR da Petrobras, medido)

```
009512ITR30-06-2026v1.xml                   5.664.876   XML estruturado
160282_009512_24082026155035.pdf            8.388.608   PDF de leitura
DadosDocumento.xlsx                            83.589
FormularioCadastral.xml                         7.600
FormularioDemonstracaoFinanceiraITR.xml         7.697
```

**O ZIP é gerado sob demanda.** Dois downloads do mesmo ITR produzem hashes
diferentes. Comparando entrada a entrada pelo CRC do diretório central:

| Entrada | Igual entre downloads |
|---|---|
| `009512ITR30-06-2026v1.xml` | sim |
| `160282_009512_{timestamp}.pdf` | **não** |
| `DadosDocumento.xlsx` | sim |
| `FormularioCadastral.xml` | sim |
| `FormularioDemonstracaoFinanceiraITR.xml` | sim |

Só o PDF difere, e o nome dele carrega o instante da geração. PDF de fato
relevante, testado do mesmo jeito, é **estável**.

Consequência: o hash é gravado **por arquivo**, com marcador de estabilidade, e
serve integridade e auditoria — nunca deduplicação.

---

## 5. Identidade e versões

`(numSequencia, numVersao)` é identidade de **publicação**, não de linhagem:
**cada reapresentação recebe um `numSequencia` novo.**

Linhagem do FRE 2026 da Petrobras, medida:

| Versão | Modalidade | Status | Entrega | numSequencia |
|---:|---|---|---|---:|
| 1 | AP | Inativo | 22/05/2026 | 157861 |
| 2 | RE | Inativo | 26/05/2026 | 157986 |
| 3 | RE | Inativo | 03/06/2026 | 158945 |
| 4 | RE | Inativo | 16/06/2026 | 159103 |
| 5 | RE | Inativo | 10/07/2026 | 159462 |
| 6 | RE | Inativo | 20/07/2026 | 159566 |
| 7 | RE | **Ativo** | 04/08/2026 | 160125 |

Três consequências:

1. **A regra "mesmo id, versão maior" nunca dispara.** O id não se repete entre
   versões.
2. **Não é preciso correlacionar nada.** A fonte marca exatamente um `Ativo` por
   linhagem e rebaixa os demais para `Inativo`. E, diferente do Fundos.NET, a
   listagem *preserva* o histórico. A supersessão vira uma coluna, não uma
   heurística.
3. **A linhagem é estrutural no `numProtocolo`:**
   `009512` · `FRE` · `2026` · `2026` · `07` · `00160125` · `-70` — código CVM,
   sigla da categoria, ano de referência, versão, sequencial, dígito.

`numProtocolo` precisa ser **persistido**, não derivado: é argumento obrigatório
do download.

Custo operacional real: sete entregas do mesmo FRE em três meses, 8,4 MB cada.

---

## 6. Vocabulário e filtros

As opções vêm **estáticas no HTML da página** — nenhuma chamada de backend:

| Combo | Opções |
|---|---:|
| `cboCategorias` | 80 |
| `cboTipo` | 112 |
| `cboEspecie` | 46 |
| `cboDocumentos` | 532 (8 `EST_*` + 524 `IPE_*_*_*`) |

Exemplos de código:

```
EST_-1  TODOS os Documentos Estruturados
EST_1   FCA - Formulário Cadastral
EST_2   FRE - Formulário de Referência
EST_3   ITR - Informações Trimestrais
EST_4   DFP - Demonstrações Financeiras Padronizadas
EST_11  Informe do Código de Governança
IPE_-1_-1_-1   TODOS os Documentos com Informações Eventuais
IPE_44_-1_-1   Acordo de Acionistas
```

**Status e Tipo de Entrega não são filtros de servidor.** No JavaScript eles só
aparecem sendo inicializados como widgets select2; nunca entram no payload. A API
sempre devolve Ativo, Inativo e Cancelado juntos, e a seleção acontece na tabela
do navegador. Para um robô isso é vantagem — um cancelamento é notícia e chega de
graça — desde que o filtro seja assumido como responsabilidade sua.

Valores desses combos, para referência:

```
cboStatusDocumento:  T=TODOS  L=Ativo  N|P|B|R=Inativo  C=Cancelado
cboApresentacao:     -1=TODOS  1=Apresentação  2=Reapresentação Espontânea
                     3=Reapresentação por Exigência
```

---

## 7. Riscos

### reCAPTCHA v3 condicional — alto

O fluxo é em duas etapas: o cliente manda `token` vazio; se o servidor responder
`SolicitarCaptcha: "S"`, o front executa `grecaptcha` com `action: 'submit'` e
repete a mesma chamada com `versaoCaptcha: "V3"`.

Em **todas** as dezenas de chamadas deste levantamento veio `"N"`, e o script do
reCAPTCHA nem estava carregado na página. Mas o gatilho é decisão do servidor,
provavelmente por volume, e não há como conhecer o limiar sem provocá-lo.

**Se vier `"S"`, não há contorno legítimo.** A saída é reduzir a frequência, não
burlar. O robô deve encerrar a execução com código de saída próprio.

### O serviço WCF por trás cai — alto

Depois de cerca de doze chamadas em poucos minutos, o backend passou a responder
`temErro: true` com *"Erro ao selecionar os documentos: The HTTP service located
at http://… is unavailable"*. A camada web continuou de pé — devolvendo até stack
trace em chamadas malformadas —, mas o serviço de documentos não voltou por cerca
de uma hora, e derrubou junto a busca de empresas.

Não dá para separar queda espontânea da CVM de circuit breaker acionado pelo
levantamento. Tratar como ambos: backoff exponencial, teto de requisições por
execução, intervalo mínimo de **5 s** (o `1.5 s` do `fii-docs-watcher` é agressivo
demais aqui), e `temErro` como retry, nunca como resultado vazio.

### Filtro de categoria errado devolve vazio, não erro — alto

`EST_3` (ITR) funciona sozinho. `EST_2` (FRE) devolve **zero linhas, sem erro** —
porque o front expande silenciosamente para `EST_2,EST_8,EST_9` antes de enviar.

Um código de filtro incorreto é indistinguível de uma companhia que não publicou
nada. Se o filtro for por categoria, a tabela de códigos precisa ser copiada do
`cboDocumentos`, nunca deduzida. Enquanto o robô pedir `EST_-1,IPE_-1_-1_-1` e
filtrar localmente, a armadilha não existe.

### Separadores literais sem escape — médio

`$&` e `$&&*` são sequências arbitrárias, não um formato com escape. Um assunto
que contenha `$&` corrompe o parse da linha inteira, silenciosamente. Validar que
cada linha tem exatamente 12 campos e derrubar a coleta se não tiver.

### Sistema recém-migrado — médio

Menos de dois meses no ar. Falhar alto e avisar quando o número de campos ou o
formato do envelope divergir, em vez de degradar em silêncio.

---

## 8. Outros PageMethods da mesma página

Não usados pelo robô, mas mapeados:

```
ListarEmpresasConsultaExterna       autocomplete de empresa
ConsultarCompanhiaAbertaCodigoCVM   parâmetro: codCVM
ConsultarCompanhiaAbertaCNPJ
PopularComboCategoria               parâmetro: empresa
PopularComboTipo
PopularComboEspecie
PopularComboTipoPorCategoria
PopularComboEspeciePorCategoriaTipo
MontarCombosCategoriaTipoEspecie
MontaAutoCompletePalavraChave
RetornarProtocoloPDF
```

Chamadas malformadas retornam **HTTP 500 com stack trace .NET** nomeando o
parâmetro faltante — útil para descobrir assinaturas, e um lembrete de que este
não é um endpoint endurecido.

---

## 9. Ainda não medido

- Onde a resposta quebra. 2.235 linhas passaram numa requisição só; não se sabe
  se existe teto.
- O limite da janela. O JavaScript chama `validaPeriodoEmAnos(2)`, sugerindo teto
  de dois anos, não confirmado.
- Quantas companhias cabem numa lista de `empresa`. Duas funcionam; a varredura
  global torna a pergunta menos urgente.
- O gatilho concreto de `SolicitarCaptcha`.
