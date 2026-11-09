const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),info=JSON.parse(fs.readFileSync(path.join(root,'.local/local-model-ui.json'),'utf8')),out=path.join(root,'docs/qa/local-model');
 fs.mkdirSync(path.join(out,'screenshots'),{recursive:true});const browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(info.url);await page.locator('#username').fill('admin');await page.locator('#password').fill('admin');await page.getByRole('button',{name:'Войти в систему'}).click();
  await page.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();await page.getByRole('button',{name:'Настройки',exact:true}).click();await page.locator('.settings-nav').getByRole('button',{name:'Модели',exact:true}).click();
  const card=page.locator('.local-model-card').filter({has:page.getByRole('heading',{name:'Qwen3.5-4B Q4_K_M',exact:true})});
  await card.getByRole('button',{name:'Скачать Qwen3.5-4B Q4_K_M',exact:true}).waitFor();await page.screenshot({path:path.join(out,'screenshots/01-lite-missing.png'),fullPage:true});
  await card.getByRole('button',{name:'Скачать Qwen3.5-4B Q4_K_M',exact:true}).click();
  const cancel=card.getByRole('button',{name:'Отменить загрузку Qwen3.5-4B Q4_K_M',exact:true});await cancel.waitFor();await cancel.click();
  await card.getByRole('button',{name:'Повторить загрузку Qwen3.5-4B Q4_K_M',exact:true}).waitFor({timeout:15000});
  await card.getByRole('button',{name:'Повторить загрузку Qwen3.5-4B Q4_K_M',exact:true}).click();
  await card.getByText('Файл проверен',{exact:true}).waitFor({timeout:15000});await page.getByRole('button',{name:'Проверить локальную модель',exact:true}).click();
  await page.getByText('Локальная модель проверена: JSON-контракт выполнен.',{exact:false}).waitFor();await page.screenshot({path:path.join(out,'screenshots/02-local-ready.png'),fullPage:true});
  await page.getByRole('button',{name:'Использовать Qwen3.5-2B Q4_K_M',exact:true}).click();await page.getByText('Активно: qwen3.5-2b-q4',{exact:true}).waitFor();
  await page.getByLabel('Адрес сервера Open WebUI',{exact:true}).fill('https://openwebui.invalid');
  await page.getByLabel('API key Open WebUI',{exact:true}).fill('PUBLIC-LOCAL-UI-KEY');await page.getByRole('button',{name:'Загрузить модели',exact:true}).click();
  await page.getByLabel('Доступная модель',{exact:true}).selectOption('qwen-external-fixture');await page.getByRole('button',{name:'Сохранить подключение',exact:true}).click();
  await page.getByText('Активно: Open WebUI',{exact:true}).waitFor();
  await page.getByRole('button',{name:'Загрузить модели',exact:true}).click();await page.getByLabel('Доступная модель',{exact:true}).selectOption('gemma-external-fixture');
  await page.getByRole('button',{name:'Сохранить подключение',exact:true}).click();
  await page.getByRole('button',{name:'Использовать профиль qwen-external-fixture',exact:true}).click();
  await page.getByRole('button',{name:'Использовать Qwen3.5-4B Q4_K_M',exact:true}).click();await page.getByText('Активно: qwen3.5-4b-q4',{exact:true}).waitFor();
  const config=await(await page.request.get(info.url+'/api/settings/providers')).json();assert(config.profiles.length===2&&config.active.provider==='local'&&!JSON.stringify(config).includes('PUBLIC-LOCAL-UI-KEY'));
  await page.screenshot({path:path.join(out,'screenshots/03-profiles-switch.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(!(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)));
  await page.screenshot({path:path.join(out,'screenshots/04-mobile-models.png'),fullPage:true});
  assert(!(await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}))).includes('PUBLIC-LOCAL-UI-KEY'));assert.deepEqual(errors,[]);
  assert(!fs.readFileSync(info.database).includes(Buffer.from('PUBLIC-LOCAL-UI-KEY')));
  fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify({scope:'synthetic UI model transfer/inference fixture',realGGUFInference:false,liteMissing:true,downloadProgressCancelRetry:true,hashReady:true,localProbe:true,local2B4BSwitch:true,twoSavedOpenWebUIProfiles:true,localExternalSwitchPreservesKeys:true,noBrowserStorageSecrets:true,noDatabaseKey:true,mobileOverflow:false,pageErrors:errors},null,2));console.log('Local model UI download/cancel/profiles passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error('Local model browser failed: '+e.message.split('Call log:')[0]);process.exit(1)});
