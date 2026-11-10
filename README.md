# Diário do Filhote · fábrica de vídeos grátis

Todo dia às 05:00 (Brasília), o GitHub produz 3 vídeos verticais (1080x1920, 25 a 35 s) do @diariodofilhote. Para cada vídeo, ele:

1. pega o próximo item com status `pauta` em `pauta.csv`
2. escreve o roteiro com o Gemini (plano grátis)
3. desenha 4 cenas do Paçoca no Pollinations (grátis, sem chave)
4. grava a narração com a voz neural pt-BR da Microsoft (edge-tts, grátis)
5. monta o vídeo com o título em 2 linhas, a etiqueta da série e a legenda palavra por palavra
6. publica o `.mp4` e o `.txt` (horário + legenda do post) em **Releases**

O script do Google (`drive/Copiador.gs`) leva cada vídeo novo para a pasta **Diário do Filhote · vídeos** no seu Drive. Toda segunda, a pauta da semana se completa sozinha.

## Configuração (uma vez)

1. **Settings → Secrets and variables → Actions → New repository secret**
   - Nome: `GEMINI_API_KEY`
   - Valor: a chave criada em https://aistudio.google.com/apikey
2. **Actions → Diário do Filhote → Run workflow** (tarefa `videos`, quantidade `1`) para testar.

Variáveis opcionais (aba *Variables*): `VIDEOS_POR_DIA` (padrão 3), `HORARIOS` (padrão `10:00,16:00,19:00`), `VOZ` (padrão `pt-BR-AntonioNeural`; outra opção: `pt-BR-FranciscaNeural`).

## Rotina de postagem (10 min por dia)

- Abra a pasta do Drive no celular.
- Para cada vídeo, poste no TikTok e no Instagram usando a legenda do `.txt`.
- Agende para o horário indicado com o agendador nativo de cada app.
- Marque **conteúdo gerado por IA**.

## Editar a pauta

Edite `pauta.csv` direto no GitHub (ícone de lápis). Status possíveis:

| Status | Significado |
|---|---|
| `pauta` | Entra na fila. |
| `pronto` | Vídeo já feito. |
| `erro` | Falhou. Veja a coluna `erro`; volte o status para `pauta` para tentar de novo. |

Se as notas começarem com `ROTEIRO PRONTO`, o título, a tag e as falas são usados exatamente como estão escritos.

## Limites do grátis

- **Gemini:** o plano grátis aguenta folgado 3 vídeos por dia.
- **Pollinations:** às vezes fica lento. Se uma imagem falhar, a cena sai com fundo laranja, sem travar o vídeo.
- **GitHub Actions:** ilimitado em repositório público.
