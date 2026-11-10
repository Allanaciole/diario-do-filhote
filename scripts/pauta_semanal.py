"""Acrescenta a pauta da semana (padrão: 21 vídeos = 3 por dia x 7) ao pauta.csv usando o Gemini."""

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gerar import FUSO, ler_pauta, log, salvar_pauta, gemini  # noqa: E402
from prompts import PROMPT_PAUTA  # noqa: E402

VIDEOS_POR_DIA = int(os.environ.get("VIDEOS_POR_DIA") or 3)
MINIMO_NA_FILA = VIDEOS_POR_DIA * 7


def main():
    linhas = ler_pauta()
    na_fila = sum(1 for l in linhas if l["status"].strip().lower() == "pauta")
    if na_fila >= MINIMO_NA_FILA:
        log(f"Já tem {na_fila} vídeos na fila. Nada a fazer.")
        return
    quantos = MINIMO_NA_FILA * 2 - na_fila

    historico = [f"{l['serie']} | {l['parte']} | {l['tema']}" for l in linhas[-80:]]
    partes_pacoca = [int(l["parte"]) for l in linhas if l["serie"] == "DIÁRIO DO PAÇOCA" and str(l["parte"]).isdigit()]
    ultima = max(partes_pacoca or [0])
    hoje = datetime.now(FUSO).strftime("%d/%m/%Y")
    pedido = (f"Hoje é {hoje}. Monte {quantos} vídeos, {VIDEOS_POR_DIA} por dia, seguindo a sequência.\n"
              f"Última parte do DIÁRIO DO PAÇOCA: {ultima}.\n\nHistórico recente (não repetir):\n" + "\n".join(historico))

    itens = gemini(PROMPT_PAUTA, pedido, max_tokens=8192).get("itens") or []
    nums = [int(m.group(1)) for l in linhas if (m := re.match(r"DF-(\d+)", l["id"]))]
    prox = max(nums or [0]) + 1
    agora = datetime.now(FUSO).strftime("%Y-%m-%d %H:%M")
    for it in itens[:quantos]:
        if not it.get("tema"):
            continue
        linhas.append({"id": f"DF-{prox:03d}", "serie": it.get("serie", "").strip(), "parte": str(it.get("parte", "")).strip(),
                       "tema": it["tema"].strip(), "notas": it.get("notas", "").strip(), "status": "pauta",
                       "agendado_para": "", "video_url": "", "erro": "", "criado_em": agora})
        prox += 1
    salvar_pauta(linhas)
    log(f"{len(itens[:quantos])} vídeos novos na pauta.")


if __name__ == "__main__":
    main()
