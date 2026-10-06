# Apuração Eleições 2026 (TSE) — servidor MCP + bot Telegram

Resultados **oficiais e parciais** das eleições brasileiras, direto dos arquivos públicos da
divulgação do TSE (`resultados.tse.jus.br`), como **tools MCP** para Claude, Cursor, VS Code e
qualquer cliente [Model Context Protocol](https://modelcontextprotocol.io) — e, opcionalmente,
como bot inline do Telegram.

- 1º turno 04/10/2026 (totalizado, validado ao vivo) · **2º turno: domingo 25/10/2026** — use `turno=2`
- Sem chave, sem cadastro: o TSE publica os JSON abertamente. Este projeto só decifra os códigos
  (`sp-c0003-e006259-u.json`…), faz cache respeitoso (≥ 45 s por arquivo, `If-Modified-Since`) e
  normaliza a resposta. Em 2026 o TSE publica o agregado UF/BR no mesmo formato "completo"
  (`dados/<uf>/<uf>-c<cargo>-e<eleicao>-u.json`) do arquivo por município; o simplificado
  (`dados-simplificados/…-r.json`, 2022) saiu do portal — a **v0.1.1** lê os dois e escolhe o que existe.
- **Neutralidade:** só números oficiais, sempre com `apurado_pct` e o horário do TSE. Nenhuma
  projeção, nenhum comentário, nenhum anúncio.
- Listado em [**awesome-mcp-brasil**](https://github.com/daniel-filius/awesome-mcp-brasil) — hub
  curado e auto-verificado de servidores MCP e skills brasileiros (categoria Eleições / Governo).

<!-- mcp-name: io.github.daniel-filius/apuracao-2026 -->

## Instalar

### Docker (imagem publicada em ghcr.io)

```bash
docker run -i --rm ghcr.io/daniel-filius/apuracao-2026-mcp:v0.1.1
```

Claude Desktop (`claude_desktop_config.json`) / Cursor / VS Code (`mcp.json`):

```json
{
  "mcpServers": {
    "apuracao-2026": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "ghcr.io/daniel-filius/apuracao-2026-mcp:v0.1.1"]
    }
  }
}
```

### Sem Docker (Python 3.11+ e [uv](https://docs.astral.sh/uv/))

```bash
uvx --from git+https://github.com/daniel-filius/apuracao-2026-mcp apuracao-mcp
```

```json
{
  "mcpServers": {
    "apuracao-2026": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/daniel-filius/apuracao-2026-mcp", "apuracao-mcp"]
    }
  }
}
```

Claude Code:

```bash
claude mcp add apuracao-2026 -- uvx --from git+https://github.com/daniel-filius/apuracao-2026-mcp apuracao-mcp
# ou
claude mcp add apuracao-2026 -- docker run -i --rm ghcr.io/daniel-filius/apuracao-2026-mcp:v0.1.1
```

Também está no [registro MCP oficial](https://registry.modelcontextprotocol.io/v0/servers?search=apuracao)
como `io.github.daniel-filius/apuracao-2026`.

## Tools

| Tool | O que devolve |
|---|---|
| `listar_eleicoes()` | Eleições ordinárias no config oficial do TSE (ciclo, código, data, turno, cargos por abrangência) e histórico disponível |
| `resultado(uf, cargo, turno=1, ano=None, limite=20)` | Parcial oficial por UF (`SP`, `RJ`…) ou `BR` (só presidente) |
| `resumo_brasil(turno=1, ano=None)` | Presidente, total nacional |
| `municipio(uf, municipio, cargo, turno=1, ano=None)` | Parcial oficial por município (nome exato, código IBGE ou código TSE) |

Cargos: `presidente`, `governador`, `senador`, `deputado_federal`, `deputado_estadual`,
`deputado_distrital`, `prefeito`, `vereador`.

Toda resposta inclui:

```json
{
  "fonte": "TSE – divulgação oficial, resultado parcial",
  "atualizado_em": "04/10/2026 19:32:11",
  "apurado_pct": 87.31,
  "matematicamente_definido": false,
  "eleicao": {"ano": 2026, "turno": 1, "codigo": "…", "ciclo": "ele2026"},
  "cargo": "Governador", "abrangencia": "SP",
  "totais": {"votos_validos": 0, "brancos": 0, "nulos": 0, "abstencao_pct": 0.0, "…": "…"},
  "candidatos": [
    {"posicao": 1, "nome": "…", "numero": "10", "partido": "…", "votos": 0, "pct": 0.0, "situacao": "…", "eleito": false}
  ]
}
```

Exemplos de pergunta ao agente: *"quem está na frente para governador do RS?"*,
*"como está a apuração para presidente?"*, *"resultado de prefeito em Campinas em 2024"*.

### 1º turno (04/10/2026) — validado ao vivo

Na noite da apuração o servidor (imagem `v0.1.1`) leu os arquivos reais do TSE. Resposta real de
`resultado("SP", "governador")`, resumida:

```json
{
  "fonte": "TSE – divulgação oficial, resultado parcial",
  "atualizado_em": "05/10/2026 11:44:29",
  "eleicao": {"ano": 2026, "turno": 1, "codigo": "6259", "ciclo": "ele2026", "nome": "Eleição Ordinária Estadual - 2026 1º Turno"},
  "cargo": "Governador", "abrangencia": "SP",
  "url_tse": "https://resultados.tse.jus.br/oficial/ele2026/6259/dados/sp/sp-c0003-e006259-u.json",
  "apurado_pct": 100.0, "secoes_totalizadas": 103656, "secoes_total": 103656,
  "totais": {"eleitorado": 34081699, "comparecimento_pct": 77.44, "votos_validos": 23130513, "brancos_pct": 5.11, "nulos_pct": 7.26},
  "candidatos": [
    {"posicao": 1, "nome": "TARCÍSIO", "numero": "10", "partido": "REPUBLICANOS", "votos": 14491874, "pct": 62.65, "situacao": "Eleito", "eleito": true},
    {"posicao": 2, "nome": "FERNANDO HADDAD", "numero": "13", "partido": "PT", "votos": 8423656, "pct": 36.42, "situacao": "Não eleito", "eleito": false}
  ]
}
```

### 2º turno — domingo 25/10/2026

Presidente (`BR`) e governador nas UFs em que ninguém passou de 50 % dos votos válidos. Basta
`turno=2`:

- *"como está a apuração do 2º turno para presidente?"* → `resumo_brasil(turno=2)`
- *"2º turno para governador do RJ"* → `resultado("RJ", "governador", turno=2)`

O TSE inclui o 2º turno no config oficial (`ele-c.json`; eleições `6258` federal e `6260`
estadual) pouco antes da votação. Até lá `turno=2` devolve uma mensagem explicando isso (não um
erro genérico); no domingo 25/10 os resultados começam a aparecer a partir das 17h (Brasília), com
`apurado_pct` subindo até a totalização. O 1º turno continua disponível com `turno=1`.

Histórico: `ano=2022` (Eleições Gerais, 1º/2º turno) e `ano=2024` (municipais).

```bash
apuracao-mcp --smoke                 # lê o config do TSE e imprime o ciclo/eleições atuais
apuracao-mcp --demo SP governador    # resultado normalizado sem iniciar o MCP
```

## Bot Telegram (opcional)

`@Apuracao2026Bot` em qualquer grupo: `@Apuracao2026Bot SP governador` ou `@Apuracao2026Bot br`.
Comandos: `/start`, `/br`, `/uf SP [cargo]`.

O bot só sobe se existir `APURACAO_BOT_TOKEN` (ver `.env.example`); sem token ele sai
imediatamente. Long polling, stdlib apenas, métricas anônimas (ids hasheados) em `metrics.jsonl`.

```bash
APURACAO_BOT_TOKEN=... apuracao-bot
```

## Desenvolvimento

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                       # fixtures reais do TSE (2022) em fixtures/
python -m apuracao_mcp.server   # stdio
```

Publicação: tag `vX.Y.Z` → workflow `publish-mcp.yml` roda os testes, publica a imagem em
`ghcr.io/daniel-filius/apuracao-2026-mcp` e registra no registro MCP oficial via GitHub OIDC
(`server.json`).

## Apoie

Projeto voluntário, sem fins políticos e sem anúncios. Se foi útil na noite da apuração,
um Pix ajuda a manter em 2028:

Pix copia-e-cola (valor livre):

```
00020101021226530014br.gov.bcb.pix0131danielfilho.workspace@gmail.com5204000053039865802BR5913DANIEL FILIUS6009SAO PAULO62160512APURACAO20266304CC54
```

## Mudanças

- **v0.1.1 (2026-10-05)** — hotfix da noite do 1º turno: o TSE deixou de publicar o agregado UF/BR
  em `dados-simplificados/…-r.json` (404 `NoSuchKey`) e passou a servi-lo em
  `dados/<uf>/<uf>-c<cargo>-e<eleicao>-u.json`. `resultado`/`resumo_brasil` agora tentam o formato
  completo, caem para o simplificado (histórico 2022) e memorizam o que funcionou; fixtures reais
  de 2026 (SP governador, BR presidente, `ele-c.json` de 05/10) entraram nos testes.
- **v0.1.0 (2026-09-28)** — primeira publicação no registro MCP (OCI em ghcr.io).

## Fonte e limites

- Dados: divulgação oficial do TSE (`https://resultados.tse.jus.br/oficial/…`). Este projeto não é
  vinculado ao TSE. Números "parciais" podem mudar até a totalização final.
- Cache: 45 s por arquivo de resultado, 5 min para o config, 1 h para a lista de municípios; sem
  varredura de todas as UFs a cada chamada.
- User-Agent identifica o projeto.

Licença MIT.
