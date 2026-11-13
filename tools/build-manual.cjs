// Rebuild the standalone HTML and screenshot gallery from Markdown.
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { chromium } = require('../.package-turbo-web/manual-tools/node_modules/playwright');
const dir = path.resolve(__dirname, '../docs/manual');
const escape = s => s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
function inline(s) {
  return escape(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').replace(/\[([^\]]+)\]\(([^)]+)\)/g,'<a href="$2">$1</a>').replace(/(?<!["=>])(http:\/\/127\.0\.0\.1:7000)/g,'<a href="$1">$1</a>');
}
const css = `:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#eef3f5;color:#20313a;font:17px/1.65 system-ui,Segoe UI,sans-serif}main{max-width:1200px;margin:auto;padding:48px 60px;background:white}h1{font-size:46px;line-height:1.15;color:#173e49}h2{margin-top:60px;padding-top:20px;border-top:2px solid #cadcdd;color:#174e59;line-height:1.3}a{color:#006b83}code{font-size:.9em;background:#eef3f5;padding:2px 5px;border-radius:4px}li{margin:8px 0}figure{margin:32px 0;break-inside:avoid}figure a{display:block}img{display:block;width:100%;height:auto;border:1px solid #bccdce;border-radius:5px}figcaption{font-size:13px;color:#52666f;margin-top:8px}nav{padding:20px 28px;background:#edf5f4;border-radius:8px}nav a{display:block}header{background:#173e49;color:#fff;padding:12px 30px;font-size:14px}header a{color:white;margin-right:24px}.gallery{display:grid;grid-template-columns:1fr 1fr;gap:24px}.gallery figure{margin:0} @media(max-width:700px){main{padding:24px}h1{font-size:34px}.gallery{grid-template-columns:1fr}} @media print{@page{size:A4 landscape;margin:14mm}body{background:white;font-size:11pt;line-height:1.45}main{max-width:none;padding:0}header,nav{display:none}h1{font-size:34pt}h2{break-after:avoid;margin-top:26px;font-size:19pt}p,li{orphans:3;widows:3}figure img{max-height:158mm;object-fit:contain;border-radius:0}figure{margin:16px 0}figcaption{font-size:9pt}a{color:inherit;text-decoration:none}}`;
const wrap = (title, body) => `<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${title}</title><style>${css}</style><header>Package Turbo documentation <a href="user-manual.html">Manual</a><a href="gallery.html">30 screenshots</a></header><main>${body}</main></html>`;
const source = fs.readFileSync(path.join(dir,'user-manual.md'),'utf8');
const lines = source.split(/\r?\n/);
let body='', paragraph=[], list=null, toc=[];
const flush=()=>{if(paragraph.length){body+=`<p>${inline(paragraph.join(' '))}</p>\n`;paragraph=[];}if(list){body+=`</${list}>\n`;list=null;}};
for (const line of lines) {
  if(!line.trim()){flush();continue;}
  const h=line.match(/^(#{1,2}) (.+)$/);
  const image=line.match(/^!\[([^\]]*)\]\(([^)]+)\)$/);
  const item=line.match(/^(?:(\d+)\.|(-)) (.+)$/);
  if(h){flush();const id=h[1].length===1?'title':`section-${toc.length}`;if(h[1].length===2)toc.push([id,h[2]]);body+=`<h${h[1].length} id="${id}">${inline(h[2])}</h${h[1].length}>\n`;if(h[1].length===1)body+='<!--contents-->';}
  else if(image){flush();if(!fs.existsSync(path.join(dir,image[2])))throw Error(`Missing image ${image[2]}`);body+=`<figure><a href="${escape(image[2])}"><img src="${escape(image[2])}" alt="${escape(image[1])}"></a><figcaption>${escape(image[1])}</figcaption></figure>\n`;}
  else if(item){if(paragraph.length){body+=`<p>${inline(paragraph.join(' '))}</p>`;paragraph=[];}const type=item[1]?'ol':'ul';if(list!==type){if(list)body+=`</${list}>`;body+=`<${type}>`;list=type;}body+=`<li>${inline(item[3])}</li>\n`;}
  else{if(list)flush();paragraph.push(line);}
}
flush();
body=body.replace('<!--contents-->',`<nav aria-label="Contents"><strong>Contents</strong>${toc.map(([id,title])=>`<a href="#${id}">${escape(title)}</a>`).join('')}</nav>`);
fs.writeFileSync(path.join(dir,'user-manual.html'),wrap('Package Turbo — User manual',body));
const files=fs.readdirSync(path.join(dir,'screenshots')).filter(f=>f.endsWith('.png')).sort();
const captions=new Map([...source.matchAll(/!\[([^\]]*)\]\(screenshots\/([^)]+)\)/g)].map(m=>[m[2],m[1]]));
fs.writeFileSync(path.join(dir,'gallery.html'),wrap('Package Turbo — Screenshot gallery',`<h1>Screenshot gallery</h1><p>${files.length} screenshots from the isolated demonstration workspace. Select any image to open it at full resolution.</p><div class="gallery">${files.map(f=>`<figure><a href="screenshots/${f}"><img loading="lazy" src="screenshots/${f}" alt="${escape(captions.get(f)||f)}"></a><figcaption>${escape(captions.get(f)||f)}</figcaption></figure>`).join('')}</div>`));
(async()=>{
  const browser=await chromium.launch({headless:true});
  try {
    const page=await browser.newPage();
    await page.goto(pathToFileURL(path.join(dir,'user-manual.html')).href);
    await page.evaluate(()=>Promise.all([...document.images].map(i=>i.decode())));
    const broken=await page.locator('img').evaluateAll(images=>images.filter(i=>!i.naturalWidth).length);
    if(broken)throw Error(`${broken} broken images`);
    await page.setViewportSize({width:1400,height:1100});
    await page.screenshot({path:path.join(dir,'manual-preview.png')});
    console.log(`Built manual HTML and gallery; verified ${await page.locator('img').count()} embedded screenshots; gallery contains ${files.length}.`);
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
