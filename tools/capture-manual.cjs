// Run after installing Playwright in .package-turbo-web/manual-tools.
// Uses an isolated data directory and random, temporary demo credentials.
const { chromium } = require('../.package-turbo-web/manual-tools/node_modules/playwright');
const { spawn, execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const root = path.resolve(__dirname, '..');
const output = path.join(root, 'docs/manual/screenshots');
fs.mkdirSync(output, { recursive: true });
const password = crypto.randomBytes(24).toString('hex');
const python = path.join(root, '.venv/Scripts/python.exe');
const hash = execFileSync(python, ['-c', 'import sys; from ph_web.auth import hash_password; print(hash_password(sys.stdin.read()))'], { input: password, cwd: root }).toString().trim();
const run = path.join(root, '.package-turbo-web', `manual-demo-${Date.now()}`);
const server = spawn(python, ['-m','uvicorn','ph_web.app:app','--host','127.0.0.1','--port','7011'], {
  cwd: root, windowsHide: true, env: { ...process.env,
    PACKAGE_TURBO_LOGIN_USERNAME: 'demo-archivist', PACKAGE_TURBO_LOGIN_PASSWORD_HASH: hash,
    PACKAGE_TURBO_OPERATOR: 'Demo archivist', PACKAGE_TURBO_DATA_DIR: run,
    PACKAGE_TURBO_STATIC_DIR: path.join(root,'web/dist/package-turbo-web/browser') }, stdio: 'ignore'
});
let browser;
async function main() {
  for (let i=0;i<80;i++) {
    try { if ((await fetch('http://127.0.0.1:7011/api/v1/health')).ok) break; } catch {}
    await new Promise(resolve=>setTimeout(resolve,250));
  }
  browser = await chromium.launch({headless:true});
  const page = await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1});
  page.on('response', async response => { if(response.status()>=400) console.log(response.status(),response.url(),await response.text()); });
  page.on('dialog', dialog=>dialog.accept());
  page.setDefaultTimeout(12000);
  const shot = async name => { await page.waitForTimeout(600); await page.screenshot({path:path.join(output,`${name}.png`)}); console.log(name); };
  const button = name => page.getByRole('button',{name,exact:true});
  await page.goto('http://127.0.0.1:7011');
  await button('Anmelden').waitFor();
  await shot('01-sign-in');
  await page.getByLabel('Benutzername',{exact:true}).fill('demo-archivist');
  await page.getByLabel('Passwort',{exact:true}).fill(password);
  await button('Anmelden').click();
  await button('Neues Paket').waitFor();
  await shot('02-empty-workspace');
  await button('Neues Paket').click();
  await page.locator('[name=office]').fill('Municipal Archives — Training');
  await page.locator('[name=creator]').fill('Public Works Department');
  await page.locator('[name=systemName]').fill('Municipal records');
  await page.locator('[name=reference]').fill('TRAINING-2026-001');
  await page.locator('[name=packageName]').fill('SIP_20261113_Training_001');
  await shot('03-create-package');
  await button('Erstellen').click();
  await page.locator('dialog').waitFor({state:'hidden',timeout:30000});
  await shot('04-package-overview');
  await page.getByRole('navigation',{name:'Editoransicht'}).getByRole('button',{name:'Statistik',exact:true}).click();
  await shot('05-statistics');
  await page.getByRole('navigation',{name:'Editoransicht'}).getByRole('button',{name:'Überblick',exact:true}).click();
  await button('Öffnen').click();
  await shot('06-open-import');
  await page.locator('input[type=file][accept=".zip,application/zip"]').setInputFiles(path.join(root,'samples/SIP_20260421_KOST_eCH1.3F_1a-3d-IO.zip'));
  await button('ZIP importieren').click();
  await page.locator('dialog').waitFor({state:'hidden',timeout:120000});
  await shot('07-imported-sip');
  fs.writeFileSync(path.join(root,'.package-turbo-web/manual-dom.txt'),await page.locator('body').innerText());
  for (const [label,name] of [['Paketinhalt','08-package-content'],['Dossiers','09-dossiers']]) { await button(label).click(); await shot(name); }
  await button('Vollständig validieren').click();
  await page.waitForTimeout(2000);
  await shot('10-validation');
  await page.getByRole('navigation',{name:'Editoransicht'}).getByRole('button',{name:'Statistik',exact:true}).click();
  await shot('11-import-statistics');
  for (const summary of ['Paket','Bearbeiten','Ansicht','Extras']) {
    await page.locator('summary').filter({hasText:new RegExp(`^${summary}$`)}).click();
    await shot(`menu-${summary.toLowerCase()}`);
    await page.keyboard.press('Escape');
    await page.locator('details[open]').evaluateAll(items=>items.forEach(i=>i.open=false));
  }
  for (const [label,name] of [['Suche','12-search'],['Notiz','13-notes'],['Änderungen','14-changes'],['Metadaten-Historie','15-history']]) {
    await button(label).last().click(); await shot(name);
  }
  await button('Suche').last().click();
  await page.getByLabel('Suchbegriff',{exact:true}).fill('Lizenz');
  await page.locator('.search-form').getByRole('button',{name:'Suchen',exact:true}).click();
  await shot('16-search-results');
  await button('Erweiterte Suche').click();
  await shot('17-advanced-search');
  await button('Stichprobe').click();
  await page.locator('[name=sampleSize]').fill('2');
  await button('Ziehen').click();
  await shot('18-sampling');
  await page.getByRole('navigation',{name:'Editoransicht'}).getByRole('button',{name:'Überblick',exact:true}).click();
  await page.getByRole('button',{name:'Ablieferungsnummer bearbeiten',exact:true}).click();
  await page.locator('app-metadata-field').filter({hasText:'Ablieferungsnummer'}).locator('textarea').fill('TRAINING-2026-002');
  await shot('19-edit-field');
  await page.locator('app-metadata-field').filter({hasText:'Ablieferungsnummer'}).locator('textarea').press('Tab');
  await page.getByRole('navigation',{name:'Werkzeugansicht'}).getByRole('button',{name:/Änderungen/}).click();
  await shot('20-pending-changes');
  await button('Speichern').click();
  await shot('21-saved-changes');
  await button('Metadaten-Historie').last().click();
  await shot('22-saved-history');
  const events=page.locator('.results tbody button');
  if(await events.count()) { await events.first().click(); await shot('23-history-event'); await page.locator('dialog').getByRole('button',{name:'Schliessen',exact:true}).click(); }
  await page.getByRole('button',{name:'Zusatzdaten bearbeiten…',exact:true}).first().click();
  await shot('24-additional-data');
  await page.locator('dialog').getByRole('button',{name:'Abbrechen',exact:true}).click();
  await button('Notiz').click();
  await page.getByLabel('Neue Notiz',{exact:true}).fill('Training review: verify delivery reference before export.');
  await button('Notiz speichern').click();
  await shot('25-note-added');
  await page.getByRole('navigation',{name:'Editoransicht'}).getByRole('button',{name:'Dossier',exact:true}).click();
  await shot('26-dossier-editor');
  fs.writeFileSync(path.join(root,'.package-turbo-web/manual-dom.txt'),await page.locator('body').innerText());
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();});
