/**
 * Diário do Filhote · copiador GitHub → Google Drive
 * De hora em hora, procura vídeos novos nas Releases do repositório
 * e salva .mp4 + .txt na pasta "Diário do Filhote · vídeos" (uma subpasta por dia).
 *
 * Uso: rode instalar() uma vez pelo editor e autorize.
 */

const REPO = 'Allanaciole/diario-do-filhote';   // troque se o repositório tiver outro nome
const PASTA = 'Diário do Filhote · vídeos';

function instalar() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('copiarVideos').timeBased().everyHours(1).create();
  copiarVideos();
  Logger.log('Pasta: ' + pasta_().getUrl());
}

function pasta_() {
  const it = DriveApp.getFoldersByName(PASTA);
  return it.hasNext() ? it.next() : DriveApp.createFolder(PASTA);
}

function subpasta_(pai, nome) {
  const it = pai.getFoldersByName(nome);
  return it.hasNext() ? it.next() : pai.createFolder(nome);
}

function copiarVideos() {
  const repo = PropertiesService.getScriptProperties().getProperty('GITHUB_REPO') || REPO;
  const resp = UrlFetchApp.fetch('https://api.github.com/repos/' + repo + '/releases?per_page=20', {
    muteHttpExceptions: true,
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'diario-do-filhote' }
  });
  if (resp.getResponseCode() !== 200) throw new Error('GitHub ' + resp.getResponseCode() + ': ' + resp.getContentText().slice(0, 200));

  const props = PropertiesService.getScriptProperties();
  const feitos = JSON.parse(props.getProperty('COPIADOS') || '[]');
  const raiz = pasta_();
  let novos = 0;

  JSON.parse(resp.getContentText()).reverse().forEach(rel => {
    rel.assets.forEach(a => {
      if (feitos.indexOf(a.id) !== -1) return;
      if (a.size > 45 * 1024 * 1024) { Logger.log('Grande demais, pulei: ' + a.name); return; }
      const arq = UrlFetchApp.fetch(a.browser_download_url, { followRedirects: true }).getBlob().setName(a.name);
      subpasta_(raiz, rel.name || rel.tag_name).createFile(arq);
      feitos.push(a.id);
      novos++;
    });
  });

  props.setProperty('COPIADOS', JSON.stringify(feitos.slice(-500)));
  Logger.log(novos + ' arquivo(s) novo(s) copiados para ' + raiz.getUrl());
}
