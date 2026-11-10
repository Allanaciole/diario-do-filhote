"""
Diário do Filhote · gerador de vídeos (versão gratuita)

Pega os próximos itens com status "pauta" no pauta.csv e, para cada um:
  1. escreve o roteiro com o Gemini (plano gratuito)
  2. gera 4 ilustrações no Pollinations (grátis, sem chave)
  3. narra com a voz neural da Microsoft (edge-tts, grátis)
  4. monta o vídeo 1080x1920 com título, etiqueta e legenda palavra a palavra (ffmpeg)
Salva em saida/<id>.mp4 + saida/<id>.txt (legenda do post) e marca o item como "pronto".
"""

import asyncio
import csv
import json
import math
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from prompts import ESTILO_IMAGEM, PROMPT_ROTEIRO  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent
PAUTA = RAIZ / "pauta.csv"
SAIDA = RAIZ / "saida"
TMP = RAIZ / "tmp"
FONTE_TITULO = str(RAIZ / "fonts" / "Baloo2.ttf")
FONTE_TEXTO = str(RAIZ / "fonts" / "Nunito.ttf")

W, H, FPS = 1080, 1920, 30
COR_MARCA = (232, 113, 26)        # #E8711A
COR_CAFE = (43, 27, 18)           # #2B1B12
BRANCO = (255, 255, 255)
ARROBA = "@diariodofilhote"

VIDEOS_POR_DIA = int(os.environ.get("VIDEOS_POR_DIA") or 3)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
VOZ = os.environ.get("VOZ") or "pt-BR-AntonioNeural"
VELOCIDADE = os.environ.get("VELOCIDADE") or "+8%"
HORARIOS = (os.environ.get("HORARIOS") or "10:00,16:00,19:00").split(",")
FUSO = timezone(timedelta(hours=-3))

COLS = ["id", "serie", "parte", "tema", "notas", "status", "agendado_para", "video_url", "erro", "criado_em"]


# ---------------------------------------------------------------- utilidades

def log(*a):
    print(datetime.now(FUSO).strftime("%H:%M:%S"), *a, flush=True)


def ler_pauta():
    with open(PAUTA, encoding="utf-8", newline="") as f:
        linhas = list(csv.DictReader(f))
    for l in linhas:
        for c in COLS:
            l.setdefault(c, "")
    return linhas


def salvar_pauta(linhas):
    with open(PAUTA, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(linhas)


def ffprobe_duracao(caminho):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(caminho)],
        capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def rodar(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg falhou:\n" + r.stderr[-2500:])


# ---------------------------------------------------------------- roteiro (Gemini)

def gemini(system, user, max_tokens=2048):
    chave = os.environ.get("GEMINI_API_KEY")
    if not chave:
        raise RuntimeError("Falta o segredo GEMINI_API_KEY no GitHub.")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    corpo = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9, "maxOutputTokens": max_tokens},
    }
    for tentativa in range(5):
        r = requests.post(url, params={"key": chave}, json=corpo, timeout=120)
        if r.status_code in (429, 500, 502, 503, 504):
            espera = 20 * (tentativa + 1)
            log(f"Gemini {r.status_code}, tentando de novo em {espera}s")
            time.sleep(espera)
            continue
        if r.status_code >= 300:
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
        partes = r.json()["candidates"][0]["content"]["parts"]
        texto = "".join(p.get("text", "") for p in partes)
        texto = re.sub(r"```(json)?", "", texto).strip()
        return json.loads(texto[texto.find("{"): texto.rfind("}") + 1])
    raise RuntimeError("Gemini indisponível depois de 5 tentativas.")


def roteiro_pronto(notas):
    """Lê o roteiro fixo das notas (ROTEIRO PRONTO :: TÍTULO: A / B :: TAG: ... :: CENA 1: ... :: CTA: ...)."""
    if not notas.startswith("ROTEIRO PRONTO"):
        return None
    partes = [p.strip() for p in notas.split("::")[1:]]
    r = {"cenas": []}
    for p in partes:
        chave, _, valor = p.partition(":")
        chave, valor = chave.strip().upper(), valor.strip()
        if chave == "TÍTULO":
            l1, _, l2 = valor.partition("/")
            r["titulo_l1"], r["titulo_l2"] = l1.strip(), l2.strip()
        elif chave == "TAG":
            r["tag"] = valor
        elif chave.startswith("CENA"):
            r["cenas"].append(valor)
        elif chave == "CTA":
            r["cta_fala"] = valor
    return r


def escrever_roteiro(item):
    pedido = (f"Série: {item['serie']}\nParte/idade: {item['parte'] or '-'}\n"
              f"Tema: {item['tema']}\nNotas: {item['notas'] or '-'}")
    fixo = roteiro_pronto(item["notas"])
    try:
        r = gemini(PROMPT_ROTEIRO, pedido)
    except Exception as e:  # noqa: BLE001
        if not fixo:
            raise
        log("Sem Gemini, usando o roteiro pronto com imagens padrão:", e)
        r = {"cenas": [], "legenda": f"{item['tema']}. E o seu filhote, como foi?",
             "hashtags": ["#filhote", "#cachorro", "#tutordepet", "#diariodofilhote"]}
    if fixo:  # garante as falas exatamente como foram escritas
        r["titulo_l1"], r["titulo_l2"], r["tag"], r["cta_fala"] = fixo["titulo_l1"], fixo["titulo_l2"], fixo["tag"], fixo["cta_fala"]
        cenas = r.get("cenas") or []
        padrao = ["the puppy looking curious at home", "the puppy playing on the living room floor",
                  "close-up of the puppy's expressive face", "the puppy resting in a cozy bed"]
        r["cenas"] = [{"fala": f, "imagem": (cenas[i].get("imagem") if i < len(cenas) else padrao[i % 4])}
                      for i, f in enumerate(fixo["cenas"])]
    if len(r.get("cenas") or []) < 3:
        raise RuntimeError("Roteiro sem cenas suficientes")
    return r


# ---------------------------------------------------------------- imagens (Pollinations)

def gerar_imagem(prompt, destino, seed):
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:1500])
    params = {"width": 1080, "height": 1920, "model": "flux", "nologo": "true", "seed": seed, "enhance": "false"}
    for tentativa in range(5):
        try:
            r = requests.get(url, params=params, timeout=240)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image") and len(r.content) > 15000:
                destino.write_bytes(r.content)
                Image.open(destino).convert("RGB").save(destino, "JPEG", quality=92)
                return True
            log(f"Pollinations {r.status_code} ({len(r.content)} bytes)")
        except requests.RequestException as e:
            log("Pollinations erro:", e)
        time.sleep(15 * (tentativa + 1))
    return False


def imagem_reserva(destino, i):
    """Se o Pollinations falhar, usa um fundo liso da marca para o vídeo não travar."""
    img = Image.new("RGB", (W, H), COR_MARCA if i % 2 == 0 else (200, 92, 18))
    img.save(destino, "JPEG", quality=92)


# ---------------------------------------------------------------- voz (edge-tts)

async def _falar(texto, mp3):
    import edge_tts
    com = edge_tts.Communicate(texto, VOZ, rate=VELOCIDADE, boundary="WordBoundary")
    palavras = []
    with open(mp3, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                ini = ch["offset"] / 1e7
                palavras.append([ini, ini + ch["duration"] / 1e7, ch["text"]])
    return palavras


def falar(texto, mp3):
    for tentativa in range(4):
        try:
            palavras = asyncio.run(_falar(texto, mp3))
            break
        except Exception as e:  # noqa: BLE001
            log("edge-tts erro:", e)
            time.sleep(10 * (tentativa + 1))
    else:
        raise RuntimeError("Não consegui gerar a voz.")
    dur = ffprobe_duracao(mp3)
    if not palavras:  # sem marcação de palavras: distribui pelo tempo
        ws = texto.split()
        passo = dur / max(len(ws), 1)
        palavras = [[i * passo, (i + 1) * passo, w] for i, w in enumerate(ws)]
    return palavras, dur


# ---------------------------------------------------------------- artes (Pillow)

def fonte(caminho, tamanho, peso):
    f = ImageFont.truetype(caminho, tamanho)
    try:
        f.set_variation_by_axes([peso])
    except Exception:  # noqa: BLE001
        pass
    return f


def png_titulo(l1, l2, destino):
    larg_max = int(W * 0.84) - 100
    tam = 132
    while tam > 60:
        f = fonte(FONTE_TITULO, tam, 800)
        if max(f.getlength(l1), f.getlength(l2)) <= larg_max:
            break
        tam -= 6
    alt_linha = int(tam * 0.98)
    caixa_w = int(W * 0.84)
    caixa_h = alt_linha * 2 + 70
    img = Image.new("RGBA", (W, caixa_h + 30), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    x0 = (W - caixa_w) // 2
    d.rounded_rectangle([x0 + 8, 14, x0 + caixa_w + 8, caixa_h + 14], radius=44, fill=(0, 0, 0, 70))
    d.rounded_rectangle([x0, 6, x0 + caixa_w, caixa_h + 6], radius=44, fill=COR_MARCA + (255,))
    y = 6 + 35
    for texto, cor in ((l1, BRANCO), (l2, COR_CAFE)):
        tw = f.getlength(texto)
        d.text(((W - tw) / 2, y - tam * 0.18), texto, font=f, fill=cor)
        y += alt_linha
    img.save(destino)
    return img.height


def png_tag(texto, destino):
    f = fonte(FONTE_TEXTO, 38, 900)
    tw = f.getlength(texto)
    pad_x, alt = 34, 66
    img = Image.new("RGBA", (int(tw + pad_x * 2), alt), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, img.width - 1, alt - 1], radius=alt // 2, fill=(43, 27, 18, 200))
    d.text((pad_x, 10), texto, font=f, fill=BRANCO)
    img.save(destino)
    return img.width


def png_arroba(destino):
    f = fonte(FONTE_TITULO, 92, 800)
    tw = f.getlength(ARROBA)
    img = Image.new("RGBA", (W, 160), (0, 0, 0, 0))
    ImageDraw.Draw(img).text(((W - tw) / 2, 10), ARROBA, font=f, fill=BRANCO)
    img.save(destino)


def grupos_de_palavras(palavras, max_palavras=3, max_chars=18):
    grupos, atual = [], []
    for p in palavras:
        texto = " ".join(x[2] for x in atual + [p])
        if atual and (len(atual) >= max_palavras or len(texto) > max_chars):
            grupos.append(atual)
            atual = []
        atual.append(p)
    if atual:
        grupos.append(atual)
    return grupos


def pngs_legenda(palavras, dur, pasta, prefixo, destaque=COR_MARCA):
    """Uma imagem por palavra falada: o grupo de palavras com a palavra atual em destaque laranja."""
    f = fonte(FONTE_TEXTO, 74, 900)
    espaco = f.getlength(" ")
    saidas = []
    grupos = grupos_de_palavras(palavras)
    todas = [p for g in grupos for p in g]
    k = 0
    for g in grupos:
        larguras = [f.getlength(p[2]) for p in g]
        total = sum(larguras) + espaco * (len(g) - 1)
        for j, atual in enumerate(g):
            img = Image.new("RGBA", (W, 170), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            x = (W - total) / 2
            for i, p in enumerate(g):
                if i == j:
                    d.rounded_rectangle([x - 16, 22, x + larguras[i] + 16, 140], radius=24, fill=destaque + (255,))
                    d.text((x, 30), p[2], font=f, fill=BRANCO)
                else:
                    d.text((x, 30), p[2], font=f, fill=BRANCO, stroke_width=7, stroke_fill=COR_CAFE)
                x += larguras[i] + espaco
            caminho = pasta / f"{prefixo}_leg{k:03d}.png"
            img.save(caminho)
            ini = atual[0]
            fim = todas[k + 1][0] if k + 1 < len(todas) else dur
            saidas.append((caminho, ini, max(fim, ini + 0.05)))
            k += 1
    return saidas


# ---------------------------------------------------------------- montagem (ffmpeg)

def montar_cena(fundo, audio, dur, overlays, destino, zoom_entrando=True, cor_fundo=None):
    """overlays: lista de (png, x, y, ini, fim) — ini/fim None = cena toda."""
    frames = max(int(math.ceil(dur * FPS)), 1)
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if cor_fundo:
        cmd += ["-f", "lavfi", "-i", f"color=c={cor_fundo}:s={W}x{H}:r={FPS}:d={dur:.3f}"]
    else:
        cmd += ["-i", str(fundo)]
    cmd += ["-i", str(audio)]
    for o in overlays:
        cmd += ["-i", str(o[0])]
    if cor_fundo:
        filtro = "[0:v]format=yuv420p[v0]"
    else:
        z = ("if(eq(on,0),1.0,min(zoom+0.0006,1.12))" if zoom_entrando
             else "if(eq(on,0),1.12,max(zoom-0.0006,1.0))")
        filtro = (f"[0:v]scale={W*2}:{H*2}:force_original_aspect_ratio=increase,crop={W*2}:{H*2},"
                  f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
                  f"format=yuv420p[v0]")
    atual = "v0"
    for i, (png, x, y, ini, fim) in enumerate(overlays):
        prox = f"v{i + 1}"
        quando = f":enable='between(t,{ini:.3f},{fim:.3f})'" if ini is not None else ""
        filtro += f";[{atual}][{i + 2}:v]overlay=x={x}:y={y}{quando}[{prox}]"
        atual = prox
    filtro += f";[1:a]apad,atrim=0:{dur:.3f},aresample=44100[a]"
    cmd += ["-filter_complex", filtro, "-map", f"[{atual}]", "-map", "[a]",
            "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ac", "2", str(destino)]
    rodar(cmd)


def juntar(cenas, destino):
    lista = destino.with_suffix(".txt.list")
    lista.write_text("".join(f"file '{c.resolve()}'\n" for c in cenas), encoding="utf-8")
    rodar(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lista),
           "-c", "copy", "-movflags", "+faststart", str(destino)])
    lista.unlink()


def produzir(item, roteiro):
    pasta = TMP / item["id"]
    pasta.mkdir(parents=True, exist_ok=True)

    # artes fixas
    tag_png = pasta / "tag.png"
    tag_w = png_tag(roteiro["tag"], tag_png)
    tag_ov = (tag_png, (W - tag_w) // 2, 110, None, None)
    titulo_png = pasta / "titulo.png"
    png_titulo(roteiro["titulo_l1"], roteiro["titulo_l2"], titulo_png)

    # imagens
    seed_base = random.randint(1, 10**6)
    imgs = []
    for i, cena in enumerate(roteiro["cenas"]):
        destino = pasta / f"img{i}.jpg"
        if not gerar_imagem(f"{ESTILO_IMAGEM} {cena['imagem']}", destino, seed_base + i):
            log("Imagem reserva na cena", i + 1)
            imagem_reserva(destino, i)
        imgs.append(destino)
        time.sleep(6)

    clipes = []
    for i, cena in enumerate(roteiro["cenas"]):
        mp3 = pasta / f"voz{i}.mp3"
        palavras, dur_voz = falar(cena["fala"], mp3)
        dur = dur_voz + 0.25
        legendas = pngs_legenda(palavras, dur, pasta, f"c{i}")
        ovs = [tag_ov]
        if i == 0:
            ovs.append((titulo_png, 0, 470, None, None))
        ovs += [(p, 0, 1330, a, b) for p, a, b in legendas]
        clipe = pasta / f"cena{i}.mp4"
        montar_cena(imgs[i], mp3, dur, ovs, clipe, zoom_entrando=(i % 2 == 0))
        clipes.append(clipe)

    # cena final (chamada)
    mp3 = pasta / "voz_cta.mp3"
    palavras, dur_voz = falar(roteiro.get("cta_fala") or "Segue o Diário do Filhote.", mp3)
    dur = dur_voz + 0.6
    arroba_png = pasta / "arroba.png"
    png_arroba(arroba_png)
    legendas = pngs_legenda(palavras, dur, pasta, "cta", destaque=COR_CAFE)
    ovs = [tag_ov, (arroba_png, 0, 720, None, None)] + [(p, 0, 1080, a, b) for p, a, b in legendas]
    clipe = pasta / "cena_cta.mp4"
    montar_cena(None, mp3, dur, ovs, clipe, cor_fundo="0xE8711A")
    clipes.append(clipe)

    SAIDA.mkdir(exist_ok=True)
    video = SAIDA / f"{item['id']}.mp4"
    juntar(clipes, video)
    return video


def legenda_post(roteiro):
    tags = " ".join(h if h.startswith("#") else "#" + h for h in (roteiro.get("hashtags") or []) if h)
    return f"{roteiro.get('legenda', '').strip()}\n\n{tags}".strip()


def horarios(n):
    agora = datetime.now(FUSO)
    saida = []
    for i in range(n):
        h, m = map(int, HORARIOS[i % len(HORARIOS)].strip().split(":"))
        quando = agora.replace(hour=h, minute=m, second=0, microsecond=0)
        if quando <= agora + timedelta(minutes=45):
            quando += timedelta(days=1)
        saida.append(quando.strftime("%d/%m %H:%M"))
    return saida


# ---------------------------------------------------------------- principal

def main():
    linhas = ler_pauta()
    fila = [l for l in linhas if l["status"].strip().lower() == "pauta"][:VIDEOS_POR_DIA]
    if not fila:
        log("Nada com status 'pauta'.")
        return
    quando = horarios(len(fila))
    feitos = []
    for item, hora in zip(fila, quando):
        log(f"== {item['id']} · {item['serie']} · {item['tema']}")
        try:
            roteiro = escrever_roteiro(item)
            video = produzir(item, roteiro)
            texto = (f"POSTAR EM: {hora}\n"
                     f"TÍTULO: {roteiro['titulo_l1']} {roteiro['titulo_l2']}\n"
                     f"Marque 'conteúdo gerado por IA' ao postar.\n\n"
                     f"LEGENDA:\n{legenda_post(roteiro)}\n")
            (SAIDA / f"{item['id']}.txt").write_text(texto, encoding="utf-8")
            item.update(status="pronto", agendado_para=hora, video_url=video.name, erro="",
                        criado_em=datetime.now(FUSO).strftime("%Y-%m-%d %H:%M"))
            feitos.append(item["id"])
            log(f"OK {video.name} ({ffprobe_duracao(video):.1f}s)")
        except Exception as e:  # noqa: BLE001
            log("ERRO", item["id"], e)
            item.update(status="erro", erro=str(e)[:300])
        salvar_pauta(linhas)
    if feitos:
        (SAIDA / "LEIA.md").write_text(
            "Vídeos do dia: " + ", ".join(feitos) +
            "\n\nCada vídeo tem um .txt com o horário sugerido e a legenda pronta.\n", encoding="utf-8")


if __name__ == "__main__":
    main()
