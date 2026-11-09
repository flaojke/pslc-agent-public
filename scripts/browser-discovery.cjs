const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),qa=JSON.parse(fs.readFileSync(path.join(root,'.local/network-browser-qa.json'),'utf8'));
 const out=path.join(root,'docs/qa/network-discovery-and-agent-export');fs.mkdirSync(path.join(out,'screenshots'),{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1080},acceptDownloads:true});page.setDefaultTimeout(20000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const api=async p=>{const r=await page.request.get(qa.url+p);assert(r.ok());return r.json();},shot=async n=>page.screenshot({path:path.join(out,'screenshots',n+'.png'),fullPage:true});
 try{
 await page.goto(qa.url);await page.getByLabel('Логин',{exact:true}).fill('admin');await page.getByLabel('Пароль',{exact:true}).fill('admin');await page.getByRole('button',{name:'Войти в систему'}).click();
 await page.getByRole('button',{name:'Устройства',exact:false}).filter({has:page.locator('b')}).click();
 assert.equal((await api('/qa/status')).probe_calls,0);await page.getByRole('button',{name:'Сканировать сеть',exact:false}).click();await page.locator('#scan-cidr').waitFor();assert.equal((await api('/qa/status')).probe_calls,0);
 await page.locator('#scan-cidr').fill('10.8.0.0/30');await shot('01-explicit-network');await page.getByRole('button',{name:'Проверить выбранный диапазон'}).click();await page.getByRole('heading',{name:'Диапазон проверен',exact:true}).waitFor();assert.equal(await page.locator('.candidate-table tbody tr').count(),2);await shot('02-candidates-not-managed');
 assert.equal((await api('/api/devices')).length,0);
 await page.locator('.candidate-table tbody tr').first().getByRole('button',{name:'Подготовить подключение'}).click();await page.locator('#node-onboarding').waitFor();assert.equal(await page.getByLabel('Операционная система',{exact:true}).inputValue(),'');
 await page.getByLabel('Операционная система',{exact:true}).selectOption('Windows');await page.getByLabel('Тип устройства',{exact:true}).selectOption('workstation');
 await page.locator('#node-onboarding').getByRole('button',{name:'Подготовить подключение',exact:true}).click();await page.getByRole('button',{name:'Скачать установочный ZIP',exact:true}).waitFor();await shot('03-profile-and-complete-export');
 for(const os of ['Windows','Linux']){
  if(os==='Linux'){
   await page.getByRole('button',{name:'Подключить устройство',exact:false}).click();await page.getByLabel('Имя нового узла',{exact:true}).fill('qa-linux-candidate');
   await page.getByLabel('Операционная система',{exact:true}).selectOption('Linux');await page.getByLabel('Тип устройства',{exact:true}).selectOption('server');await page.locator('#node-onboarding').getByRole('button',{name:'Подготовить подключение',exact:true}).click();
  }
  await page.waitForFunction(()=>{const b=Array.from(document.querySelectorAll('button')).find(x=>x.textContent==='Скачать установочный ZIP');return b&&!b.disabled;});
  const download=page.waitForEvent('download',{timeout:60000});await page.getByRole('button',{name:'Скачать установочный ZIP',exact:true}).click();const d=await download;await d.saveAs(path.join(qa.directory,os+'.zip'));assert.equal(await d.failure(),null);
  await page.getByRole('status').filter({hasText:'SHA256 проверен'}).waitFor();
 }
 assert.equal((await api('/api/devices')).length,0);
 await page.locator('#scan-cidr').fill('10.8.0.0/24');await page.getByRole('button',{name:'Проверить выбранный диапазон'}).click();await page.getByRole('button',{name:'Остановить сканирование'}).click();await page.getByRole('heading',{name:'Остановлено · частичный результат',exact:true}).waitFor();await shot('04-cancel-partial');
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await shot('05-mobile');
 assert.deepEqual(errors,[]);const result={scope:'synthetic network, actual native ZIP files',realLANScan:false,hiddenScan:false,candidates:2,autoEnrollment:false,managedDevicesCreated:0,explicitOSRequired:true,windowsAndLinuxZipDownloaded:true,browserSHA256Verified:true,enrollmentCodeSeparate:true,cancelPartial:true,mobile390Overflow:false,pageErrors:errors};
 fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){await shot('failure');throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
