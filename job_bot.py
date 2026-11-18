"""
Bot de vagas para desenvolvedores (Brasil).

- Le seu CV (PDF ou TXT) e extrai suas habilidades
- Busca vagas em Remotive, Greenhouse e Lever
- Filtra por localizacao: remoto que aceite Brasil OU presencial/hibrido na sua cidade
- Filtra por data: so vagas publicadas nos ultimos N dias
- Calcula o match (%) entre CV e vaga e recomenda so >= MATCH_MINIMO
- Da um veredito de confianca (fonte + sinais de golpe)
- Envia as vagas novas para o seu Telegram, com botao para abrir a vaga
- Modo --loop: repete a busca a cada hora (so avisa vagas novas)
- Guarda vagas ja recomendadas em SQLite para nao repetir

Uso:
    python job_bot.py --cv cv.pdf
    python job_bot.py --cv cv.pdf --telegram
    python job_bot.py --cv cv.pdf --telegram --loop              (a cada 60 min)
    python job_bot.py --cv cv.pdf --telegram --loop --intervalo 30 --dias 3
    python job_bot.py --descobrir-chat      (para achar seu chat id do Telegram)
"""
import argparse
import json
import os
import re
import sqlite3
import time
import unicodedata
import webbrowser
from datetime import datetime, timedelta, timezone

import requests

MATCH_MINIMO = 75          # percentual minimo para recomendar
CIDADE_PADRAO = "Rio de Janeiro"
DIAS_PADRAO = 7            # so vagas publicadas nos ultimos N dias
INTERVALO_PADRAO = 60      # minutos entre verificacoes no modo --loop
MAX_ENVIOS_POR_CICLO = 15  # limite de mensagens por verificacao (o resto vai na proxima)

# Dicionario de habilidades que o bot reconhece (adicione as suas)
SKILLS = [
    "react", "react native", "nextjs", "angular", "vue", "node", "express",
    "typescript", "javascript", "python", "fastapi", "django", "flask",
    "html", "css", "tailwind", "redux",
    "postgresql", "mongodb", "mysql", "sql", "prisma", "mongoose", "redis",
    "docker", "docker compose", "git", "github", "ci/cd", "aws", "linux",
    "rest", "api", "graphql", "jwt", "oauth",
    "llm", "rag", "openai", "langchain",
    "jest", "pytest", "kafka", "rabbitmq",
    "java", "php", "ruby", "rails", "rust", "golang", "c#", ".net",
    "kotlin", "swift", "scala", "elixir",
]

# Empresas com quadro publico em Greenhouse / Lever (adicione as que quiser)
GREENHOUSE = ["nubank", "vtex", "gitlab"]
LEVER = []  # adicione empresas que usam Lever (o nome que aparece em jobs.lever.co/NOME)

# Termos de busca na Gupy (portal de vagas brasileiro)
GUPY_TERMOS = ["desenvolvedor", "full stack", "frontend", "backend", "react", "node"]

GUPY_URLS = ["https://employability-portal.gupy.io/api/v1/jobs",
             "https://portal.api.gupy.io/api/v1/jobs"]

# Cabecalho para as consultas (alguns sites recusam requisicoes sem identificacao)
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/120 Safari/537.36",
           "Accept": "application/json"}

# Fontes que o bot considera confiaveis
FONTES_CONFIAVEIS = {"gupy", "remotive", "greenhouse", "lever"}

# Filtro de titulo (aplicado ao titulo sem acentos e em minusculas)
TITULO_DEV = re.compile(
    r"(developer|engineer|desenvolv|programador|software|front.?end|back.?end|full.?stack|mobile|\bdev\b)")
TITULO_EXCLUI = re.compile(
    r"\b(senior|sr|staff|principal|lead|head|manager|director|sales|account|marketing|"
    r"recruit\w*|designer|support|legal|counsel|finance|customer|partner|executive|"
    r"solutions|security|data scientist|machine learning|devops|sre|"
    r"especialista|coordenador\w*|gerente|lider|arquiteto|vendas|comercial)\b")
MIN_SKILLS_NA_VAGA = 3   # a vaga precisa citar pelo menos 3 tecnologias
MIN_SKILLS_EM_COMUM = 3  # e voce precisa ter pelo menos 3 delas

# Localizacao: remoto so vale se a vaga aceitar candidatos do Brasil
BRASIL_RE = re.compile(
    r"\b(brazil|brasil|latam|latin america|south america|americas|worldwide|anywhere|everywhere)\b")

# Sinais tipicos de golpe em anuncios de vaga
SINAIS_GOLPE = [
    r"taxa de (inscri|cadastro|treinamento)", r"pagamento antecipado",
    r"ganhos? (de )?r?\$?\s?\d+.*por (dia|hora)", r"sem experi[eê]ncia.*ganhe",
    r"enviar (documentos|cpf|rg|senha)", r"whatsapp", r"telegram",
    r"investimento inicial", r"trabalhe de casa.*f[aá]cil",
]


def sem_acento(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return s.encode("ascii", "ignore").decode().lower()


def parse_data(valor):
    """Converte data ISO ou epoch em milissegundos para datetime UTC (ou None)."""
    if not valor:
        return None
    try:
        if isinstance(valor, (int, float)):
            return datetime.fromtimestamp(valor / 1000, tz=timezone.utc)
        d = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OSError):
        return None


def idade_vaga(vaga: dict) -> str:
    d = vaga.get("publicada")
    if not d:
        return "data nao informada"
    delta = datetime.now(timezone.utc) - d
    horas = int(delta.total_seconds() // 3600)
    if horas < 1:
        return "publicada ha poucos minutos"
    if horas < 24:
        return f"publicada ha {horas}h"
    return f"publicada ha {delta.days} dia(s)"


# ---------- CV ----------
def ler_cv(caminho: str) -> str:
    if caminho.lower().endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(p.extract_text() or "" for p in PdfReader(caminho).pages)
    with open(caminho, encoding="utf-8") as f:
        return f.read()


def extrair_skills(texto: str) -> set[str]:
    texto = texto.lower()
    # normaliza variacoes para o mesmo nome
    for antigo, novo in [("node.js", "node"), ("nodejs", "node"),
                         ("next.js", "nextjs"), ("react.js", "react"),
                         ("apis", "api"), ("postgres ", "postgresql "),
                         ("tailwind css", "tailwind"), ("ruby on rails", "ruby rails"),
                         ("dotnet", ".net"), ("c sharp", "c#")]:
        texto = texto.replace(antigo, novo)
    return {s for s in SKILLS if re.search(rf"(?<![\w.]){re.escape(s)}(?![\w.])", texto)}


# ---------- Fontes de vagas ----------
# Cada vaga e um dict: fonte, id, titulo, empresa, url, texto, local, remoto, publicada
def limpar_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "")


def vagas_gupy():
    vistos = set()
    for termo in GUPY_TERMOS:
        try:
            r = None
            for base in GUPY_URLS:
                r = requests.get(base, params={"jobName": termo, "limit": 100, "offset": 0},
                                 headers=HEADERS, timeout=20)
                if r.ok:
                    break
            if not r.ok:
                print(f"[aviso] gupy/{termo}: resposta {r.status_code} "
                      f"(a Gupy pode estar recusando consultas automaticas)")
                continue
            for j in r.json().get("data", []):
                if j["id"] in vistos:
                    continue
                vistos.add(j["id"])
                local = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)
                remoto = bool(j.get("isRemoteWork")) or j.get("workplaceType") == "remote"
                yield dict(fonte="gupy", id=f"gupy-{j['id']}", titulo=j["name"],
                           empresa=j.get("careerPageName") or "empresa", url=j["jobUrl"],
                           texto=limpar_html(j.get("description")), local=local, remoto=remoto,
                           publicada=parse_data(j.get("publishedDate")))
        except Exception as e:
            print(f"[aviso] gupy/{termo}: {e}")


def vagas_remotive():
    r = requests.get("https://remotive.com/api/remote-jobs",
                     params={"category": "software-dev"}, timeout=20)
    for j in r.json().get("jobs", []):
        yield dict(fonte="remotive", id=f"remotive-{j['id']}", titulo=j["title"],
                   empresa=j["company_name"], url=j["url"],
                   texto=limpar_html(j.get("description")),
                   local=j.get("candidate_required_location", ""), remoto=True,
                   publicada=parse_data(j.get("publication_date")))


def vagas_greenhouse():
    for empresa in GREENHOUSE:
        try:
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{empresa}/jobs",
                params={"content": "true"}, timeout=20)
            for j in r.json().get("jobs", []):
                local = (j.get("location") or {}).get("name", "")
                remoto = "remote" in sem_acento(local) or "remoto" in sem_acento(local)
                yield dict(fonte="greenhouse", id=f"gh-{j['id']}", titulo=j["title"],
                           empresa=empresa, url=j["absolute_url"],
                           texto=limpar_html(j.get("content")), local=local, remoto=remoto,
                           publicada=parse_data(j.get("first_published") or j.get("updated_at")))
        except Exception as e:
            print(f"[aviso] greenhouse/{empresa}: {e}")


def vagas_lever():
    for empresa in LEVER:
        try:
            r = requests.get(f"https://api.lever.co/v0/postings/{empresa}",
                             params={"mode": "json"}, timeout=20)
            dados = r.json()
            if not isinstance(dados, list):
                print(f"[aviso] lever/{empresa}: empresa nao encontrada (confira jobs.lever.co/{empresa})")
                continue
            for j in dados:
                local = (j.get("categories") or {}).get("location", "") or ""
                remoto = j.get("workplaceType") == "remote" or "remote" in sem_acento(local)
                yield dict(fonte="lever", id=f"lever-{j['id']}", titulo=j["text"],
                           empresa=empresa, url=j["hostedUrl"],
                           texto=j.get("descriptionPlain", ""), local=local, remoto=remoto,
                           publicada=parse_data(j.get("createdAt")))
        except Exception as e:
            print(f"[aviso] lever/{empresa}: {e}")


# ---------- Localizacao ----------
def local_compativel(vaga: dict, cidade: str) -> bool:
    """Remoto: precisa aceitar Brasil. Presencial/hibrido: precisa ser na sua cidade."""
    local = sem_acento(vaga["local"])
    if vaga["remoto"]:
        return bool(BRASIL_RE.search(local))
    return sem_acento(cidade) in local


def descricao_local(vaga: dict) -> str:
    tipo = "Remoto" if vaga["remoto"] else "Presencial/Hibrido"
    return f"{tipo} - {vaga['local'] or 'local nao informado'}"


# ---------- Match e veredito ----------
def calcular_match(skills_cv: set[str], vaga: dict) -> tuple[int, set[str]]:
    texto = f"{vaga['titulo']} {vaga['texto']}".lower()
    exigidas = extrair_skills(texto)
    if len(exigidas) < MIN_SKILLS_NA_VAGA:
        return 0, set()
    em_comum = exigidas & skills_cv
    if len(em_comum) < MIN_SKILLS_EM_COMUM:
        return 0, set()
    return round(100 * len(em_comum) / len(exigidas)), em_comum


def veredito(vaga: dict) -> tuple[str, list[str]]:
    motivos = []
    if vaga["fonte"] not in FONTES_CONFIAVEIS:
        motivos.append("fonte fora da lista confiavel")
    texto = f"{vaga['titulo']} {vaga['texto']}".lower()
    for padrao in SINAIS_GOLPE:
        if re.search(padrao, texto):
            motivos.append(f"sinal suspeito: {padrao}")
    if not vaga["url"].startswith("https://"):
        motivos.append("link sem HTTPS")
    if not motivos:
        return "CONFIAVEL", []
    return ("CUIDADO" if len(motivos) == 1 else "SUSPEITA"), motivos


# ---------- Candidatura assistida ----------
def mensagem_candidatura(vaga: dict, em_comum: set[str], nome: str) -> str:
    skills = ", ".join(sorted(em_comum))
    return (
        f"Ola, equipe {vaga['empresa']}!\n\n"
        f"Tenho interesse na vaga de {vaga['titulo']}. Trabalho com {skills} "
        f"e acredito que meu perfil se encaixa no que voces procuram.\n\n"
        f"Meu GitHub e LinkedIn estao no CV em anexo. Fico a disposicao.\n\n"
        f"Atenciosamente,\n{nome}"
    )


# ---------- Telegram ----------
def ler_telegram():
    """Variaveis TELEGRAM_TOKEN / TELEGRAM_CHAT_ID (servidor) ou telegram.txt
    (linha 1 = token do bot, linha 2 = seu chat id)."""
    token_env = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_env = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token_env and chat_env:
        return token_env, chat_env
    try:
        with open("telegram.txt", encoding="utf-8-sig") as f:
            linhas = [l.strip() for l in f if l.strip()]
    except FileNotFoundError:
        return None, None
    token = linhas[0] if len(linhas) > 0 else None
    chat_id = linhas[1] if len(linhas) > 1 else None
    return token, chat_id


def enviar_telegram(token: str, chat_id: str, texto: str, url: str | None = None):
    dados = {"chat_id": chat_id, "text": texto[:4000], "disable_web_page_preview": "true"}
    if url:
        dados["reply_markup"] = json.dumps(
            {"inline_keyboard": [[{"text": "Abrir vaga", "url": url}]]})
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data=dados, timeout=20)
        if not r.ok:
            print(f"[aviso] telegram: {r.status_code} {r.text[:150]}")
    except Exception as e:
        print(f"[aviso] telegram: {e}")


def descobrir_chat():
    token, _ = ler_telegram()
    if not token:
        print("Crie o arquivo telegram.txt com o token do bot na primeira linha.")
        return
    me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20).json()
    usuario = (me.get("result") or {}).get("username", "?")
    print(f"Este token pertence ao bot @{usuario}")
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    if not r.get("ok"):
        print(f"O Telegram recusou o token ({r.get('description', 'erro desconhecido')}).")
        print("Confira a primeira linha do telegram.txt: o token precisa ser o mais recente "
              "(se voce usou /revoke, o antigo nao vale mais) e estar sem espacos ou aspas.")
        return
    chats = {u["message"]["chat"]["id"] for u in r.get("result", []) if "message" in u}
    if not chats:
        print(f"Nenhuma mensagem encontrada para @{usuario}. Abra EXATAMENTE esse bot no "
              f"Telegram (t.me/{usuario}), aperte Iniciar, mande 'oi' e rode de novo.")
        return
    for c in chats:
        print(f"Seu chat id: {c}  (coloque na segunda linha do telegram.txt)")


# ---------- Banco ----------
def abrir_banco():
    db = sqlite3.connect("vagas.db")
    db.execute("CREATE TABLE IF NOT EXISTS vistas (id TEXT PRIMARY KEY)")
    return db


# ---------- Um ciclo de busca ----------
def executar_ciclo(args, skills_cv, tg_token, tg_chat):
    db = abrir_banco()
    achadas = 0
    enviados = 0
    limite_data = datetime.now(timezone.utc) - timedelta(days=args.dias)
    for fonte in (vagas_remotive, vagas_greenhouse, vagas_lever):
        try:
            for vaga in fonte():
                titulo = sem_acento(vaga["titulo"])
                if not TITULO_DEV.search(titulo):
                    continue
                if not args.senior and TITULO_EXCLUI.search(titulo):
                    continue
                if vaga["publicada"] and vaga["publicada"] < limite_data:
                    continue
                if not local_compativel(vaga, args.cidade):
                    continue
                if db.execute("SELECT 1 FROM vistas WHERE id=?", (vaga["id"],)).fetchone():
                    continue
                pct, em_comum = calcular_match(skills_cv, vaga)
                if pct < args.minimo:
                    continue
                v, motivos = veredito(vaga)
                enviar = bool(tg_token) and v != "SUSPEITA"
                if enviar and enviados >= MAX_ENVIOS_POR_CICLO:
                    continue  # fica para a proxima verificacao
                db.execute("INSERT INTO vistas VALUES (?)", (vaga["id"],))
                achadas += 1
                print(f"[{pct}%] {vaga['titulo']} - {vaga['empresa']}")
                print(f"  local: {descricao_local(vaga)} | {idade_vaga(vaga)}")
                print(f"  veredito: {v} {'; '.join(motivos)}")
                print(f"  link: {vaga['url']}")
                if v == "CONFIAVEL":
                    print("  --- mensagem pronta ---")
                    print("  " + mensagem_candidatura(vaga, em_comum, args.nome).replace("\n", "\n  "))
                    if args.abrir:
                        webbrowser.open(vaga["url"])
                print()
                if enviar:
                    selo = "\u2705 CONFIAVEL" if v == "CONFIAVEL" else "\u26a0\ufe0f CUIDADO: " + "; ".join(motivos)
                    enviar_telegram(tg_token, tg_chat, (
                        f"\U0001F4BC {vaga['titulo']}\n"
                        f"\U0001F3E2 {vaga['empresa']}\n"
                        f"\U0001F4CD {descricao_local(vaga)}\n"
                        f"\U0001F552 {idade_vaga(vaga)}\n"
                        f"\U0001F3AF Match: {pct}% ({', '.join(sorted(em_comum))})\n"
                        f"{selo}"), url=vaga["url"])
                    enviados += 1
        except Exception as e:
            print(f"[aviso] {fonte.__name__}: {e}")
        db.commit()
    db.commit()
    db.close()
    print(f"{achadas} vaga(s) nova(s) com match >= {args.minimo}%.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cv")
    ap.add_argument("--nome", default="Caio Luan")
    ap.add_argument("--cidade", default=CIDADE_PADRAO,
                    help="sua cidade, para vagas presenciais/hibridas")
    ap.add_argument("--minimo", type=int, default=MATCH_MINIMO)
    ap.add_argument("--dias", type=int, default=DIAS_PADRAO,
                    help="so vagas publicadas nos ultimos N dias")
    ap.add_argument("--senior", action="store_true",
                    help="inclui vagas Senior/Lead/Staff e de outras funcoes tecnicas")
    ap.add_argument("--telegram", action="store_true",
                    help="envia as vagas recomendadas para o seu Telegram")
    ap.add_argument("--loop", action="store_true",
                    help="repete a busca continuamente (deixe o terminal aberto)")
    ap.add_argument("--intervalo", type=int, default=INTERVALO_PADRAO,
                    help="minutos entre buscas no modo --loop (padrao 60)")
    ap.add_argument("--descobrir-chat", action="store_true",
                    help="mostra seu chat id do Telegram")
    ap.add_argument("--abrir", action="store_true",
                    help="abre no navegador as vagas recomendadas")
    args = ap.parse_args()

    if args.descobrir_chat:
        descobrir_chat()
        return
    if not args.cv:
        ap.error("informe o CV com --cv cv.pdf")

    tg_token, tg_chat = (None, None)
    if args.telegram:
        tg_token, tg_chat = ler_telegram()
        if not (tg_token and tg_chat):
            print("[aviso] telegram.txt incompleto (token na linha 1, chat id na linha 2). "
                  "Sem envio para o Telegram.\n")
            tg_token = None

    skills_cv = extrair_skills(ler_cv(args.cv))
    print(f"Habilidades encontradas no CV: {', '.join(sorted(skills_cv)) or 'nenhuma'}")
    print(f"Cidade: {args.cidade} | remoto so se aceitar Brasil | ultimos {args.dias} dias\n")

    if tg_token and not os.environ.get("GITHUB_ACTIONS"):
        modo = f"a cada {args.intervalo} min" if args.loop else "uma vez"
        enviar_telegram(tg_token, tg_chat,
                        f"\U0001F916 Bot de vagas ligado ({modo}). Aviso quando achar vagas compativeis.")

    try:
        while True:
            print(f"=== Verificacao {datetime.now():%d/%m %H:%M} ===")
            try:
                executar_ciclo(args, skills_cv, tg_token, tg_chat)
            except Exception as e:
                print(f"[erro] {e}")
            if not args.loop:
                break
            print(f"Proxima verificacao em {args.intervalo} min. (Ctrl+C para parar)\n")
            time.sleep(args.intervalo * 60)
    except KeyboardInterrupt:
        print("\nBot parado.")


if __name__ == "__main__":
    main()
