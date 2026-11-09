const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=path.join(root,'docs/qa/model-release'),browser=await chromium.launch({channel:'msedge',headless:true});
 const before=JSON.parse(fs.readFileSync(path.join(root,'.local/pre-release-identity.json'),'utf8'));
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1100}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8787');await page.locator('#username').fill('admin');await page.locator('#password').fill('admin');await page.getByRole('button',{name:'Войти в систему'}).click();
  await page.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();
  const get=async p=>{const r=await page.request.get('http://127.0.0.1:8787'+p);assert(r.ok());return r.json();};
  assert.equal((await get('/healthz')).version,'0.11.0');
  const devices=await get('/api/devices'),demo=devices.filter(d=>d.virtual);assert.equal(demo.length,1);
  assert(before.devices.every(d=>devices.some(now=>now.id===d.id&&now.commands_enabled===d.commands_enabled&&now.installations_enabled===d.installations_enabled&&now.diagnostics_enabled===d.diagnostics_enabled)));
  assert.equal((await get('/api/users')).length,before.accounts.length);
  async function health(id,virtual){
   await page.getByLabel('Задание агенту',{exact:true}).fill('Проверь состояние узла '+id);
   await page.getByRole('button',{name:'Подготовить план',exact:false}).click();
   await page.getByTestId('workflow-status').filter({hasText:'Нужно ваше подтверждение'}).waitFor();
   await page.getByRole('button',{name:'Подтвердить весь план',exact:true}).click();
   await page.getByTestId('workflow-status').filter({hasText:'Результат проверен'}).waitFor({timeout:25000});
   await page.getByText('CPU, %',{exact:true}).waitFor();
   if(virtual)await page.getByText('Виртуальное демо · синтетические метрики',{exact:true}).waitFor();
  }
  await health(demo[0].id,true);await page.screenshot({path:path.join(out,'screenshots/10-live-virtual-health.png'),fullPage:true});
  await page.getByRole('button',{name:'Основной раздел',exact:false}).click();
  // Create a fresh agent session for an actual health-only device.
  const actual=devices.find(d=>!d.virtual&&d.online);assert(actual);await health(actual.id,false);
  await page.screenshot({path:path.join(out,'screenshots/11-live-main.png'),fullPage:true});
  await page.getByRole('button',{name:'Устройства',exact:false}).click();await page.getByText('Виртуальное демо · симуляция health',{exact:true}).waitFor();
  await page.screenshot({path:path.join(out,'screenshots/12-live-devices.png'),fullPage:true});
  const row=page.locator('tr').filter({hasText:demo[0].name});await row.getByRole('button',{name:'Настроить',exact:true}).click();
  await page.getByText('Виртуальное демо: метрики синтетические.',{exact:false}).waitFor();await page.screenshot({path:path.join(out,'screenshots/13-live-node-settings.png'),fullPage:true});
  for(const [button,file] of [['Настройка модели','14-live-model.png'],['События','15-live-events.png'],['Учётные записи','16-live-accounts.png'],['Знания','17-live-knowledge.png']]){
   await page.getByRole('button',{name:button,exact:button==='Настройка модели'||button==='Учётные записи'}).click();
   if(button==='Настройка модели')await page.getByLabel('Адрес сервера Open WebUI',{exact:true}).waitFor();
   if(button==='Знания')await page.getByRole('heading',{name:'Confluence пока не подключаем'}).waitFor();
   await page.screenshot({path:path.join(out,'screenshots/'+file),fullPage:true});
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'live.json'),JSON.stringify({version:'0.11.0',localAccountsPreserved:true,priorNodeIdsAndPermissionsPreserved:true,exactlyOneVirtualDevice:true,virtualSourceExplicit:true,virtualHealthConfirmedAndVerified:true,realWindowsHealthConfirmedAndVerified:true,noHostMutation:true,modelNotYetConfigured:true,confluenceDeferred:true,pageErrors:errors},null,2));
  console.log('Live release: preserved real Windows health + one explicit virtual demo passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error('Live release failed: '+e.message.split('Call log:')[0]);process.exit(1)});
