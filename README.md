# Bot de Vagas

Bot em Python que lê o seu currículo, procura vagas de desenvolvedor em várias fontes e manda no **Telegram** só as que combinam com você. Roda sozinho no **GitHub Actions**, de hora em hora, sem precisar deixar o computador ligado.

## Como funciona

1. Lê o `CV.PDF` e extrai as suas habilidades (React, Node, Python, etc.).
2. Busca vagas nas fontes abaixo.
3. Filtra:
   - **Cargo:** só vagas de desenvolvimento, sem Sênior/Lead/Staff e outras funções.
   - **Data:** só vagas publicadas nos últimos 7 dias.
   - **Local:** remoto que aceite candidato do Brasil, **ou** presencial/híbrido no Rio de Janeiro.
4. Calcula o **match** (habilidades suas ÷ habilidades que a vaga cita). Só avisa se for **70% ou mais**.
5. Dá um veredito (`CONFIAVEL`, `CUIDADO` ou `SUSPEITA`) com base na fonte e em sinais de golpe. Vagas suspeitas não são enviadas.
6. Manda a vaga no Telegram com um botão **Abrir vaga**.
7. Guarda as vagas já vistas no `vagas.db` para nunca repetir.

## Fontes de vagas

| Fonte | Tipo | Frequência |
|---|---|---|
| Gupy | Vagas no Brasil | toda execução |
| Remotive | Remoto (mundo) | a cada 6h |
| RemoteOK | Remoto (mundo) | a cada 3h |
| We Work Remotely | Remoto (mundo) | toda execução |
| Greenhouse | Empresas (nubank, vtex, gitlab) | toda execução |
| Lever | Empresas (lista vazia) | toda execução |

LinkedIn e Indeed **não** são usados: não têm acesso aberto e os termos deles proíbem coleta automática. Para eles, use os alertas de vaga do próprio site.

## Arquivos

| Arquivo | Para que serve |
|---|---|
| `job_bot.py` | O bot |
| `requirements.txt` | Bibliotecas Python |
| `CV.PDF` | Seu currículo (o bot usa o primeiro `.pdf` da pasta) |
| `vagas.db` | Vagas já vistas (o bot atualiza sozinho) |
| `.github/workflows/vagas.yml` | Agendamento no GitHub Actions |

## Configuração no GitHub

1. Crie o bot no Telegram com o **@BotFather** (`/newbot`) e guarde o **token**.
2. Mande "oi" para o seu bot. Sem isso ele não consegue te escrever.
3. Descubra o seu **chat id** com o **@userinfobot** (`/start`).
4. No repositório: **Settings → Secrets and variables → Actions → New repository secret**, e crie:
   - `TELEGRAM_TOKEN`: o token do BotFather (só ele, sem aspas, sem espaços, sem `bot` na frente);
   - `TELEGRAM_CHAT_ID`: o número do passo 3.
5. Em **Actions → Bot de vagas → Run workflow**, rode uma vez para testar.

Nunca coloque o token no código nem em arquivo do repositório.

## Rodando no seu computador (opcional)

```bash
pip install -r requirements.txt

# teste sem Telegram
python job_bot.py --cv CV.PDF

# com Telegram, repetindo a cada hora (deixe o terminal aberto)
# crie um telegram.txt com o token na linha 1 e o chat id na linha 2 (não suba esse arquivo!)
python job_bot.py --cv CV.PDF --telegram --loop
```

Opções úteis:

| Opção | O que faz |
|---|---|
| `--minimo 60` | Muda o match mínimo (padrão 70) |
| `--dias 14` | Aceita vagas dos últimos 14 dias (padrão 7) |
| `--cidade "São Paulo"` | Cidade para vagas presenciais/híbridas |
| `--senior` | Inclui vagas Sênior/Lead e outras funções técnicas |
| `--intervalo 30` | Minutos entre buscas no modo `--loop` |
| `--descobrir-chat` | Mostra o seu chat id do Telegram |

## Como ler o log

No GitHub: **Actions → execução → buscar → Rodar o bot**. Cada fonte mostra o funil:

```
[remoteok] lidas 97 | dev 17 | recentes 1 | local ok 0 | ainda nao vistas 0 | match ok 0
```

- **lidas:** vagas que a fonte devolveu.
- **dev:** passaram no filtro de cargo.
- **recentes:** estão dentro da janela de dias.
- **local ok:** remoto aceitando Brasil, ou na sua cidade.
- **ainda nao vistas:** não foram enviadas antes.
- **match ok:** passaram no match mínimo (essas vão para o Telegram).

A última linha resume: `N vaga(s) nova(s) com match >= 70%.`

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `telegram: 404` | Token mal copiado (espaço, aspas, `bot` na frente, linha errada) |
| `telegram: 401 Unauthorized` | Token revogado ou desatualizado. Gere outro no BotFather |
| `telegram: 400 chat not found` | Chat id errado, ou você não mandou "oi" para o bot |
| `gupy ... 404` | A Gupy mudou o endereço da API de novo |
| Execução verde, mas sem vagas | Normal: os filtros são rígidos. Baixe `--minimo` ou aumente `--dias` |
| Poucas execuções por dia | O GitHub atrasa agendamentos. O `cron` usa `17 * * * *` para ajudar |

## Ajustes no código (`job_bot.py`)

- `MATCH_MINIMO`: match mínimo.
- `SKILLS`: lista de habilidades que o bot reconhece. Adicione as suas.
- `GREENHOUSE` e `LEVER`: empresas a acompanhar (o nome que aparece em `boards.greenhouse.io/NOME` ou `jobs.lever.co/NOME`).
- `GUPY_TERMOS`: termos pesquisados na Gupy.
- `MAX_ENVIOS_POR_CICLO`: limite de mensagens por rodada (o resto vai na próxima).

## Aviso

O match é feito por palavras-chave. Leia a vaga antes de se candidatar e confirme se a empresa contrata no Brasil.
