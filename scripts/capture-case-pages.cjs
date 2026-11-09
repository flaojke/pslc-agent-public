const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),out=path.join(root,process.env.PSLC_CASE_CAPTURE_DIR||'docs/qa/dialogue-case-workspace/screenshots'),browser=await chromium.launch({channel:'msedge',headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1440,height:1080},bypassCSP:true});await page.goto('http://127.0.0.1:8787');await page.getByLabel('Логин',{exact:true}).fill('admin');await page.getByLabel('Пароль',{exact:true}).fill('admin');await page.getByRole('button',{name:'Войти в систему'}).click();
 const shot=async n=>page.screenshot({path:path.join(out,n+'.png'),fullPage:true});
 const chat=page.locator('.conversation-name').filter({hasText:'Проверка состояния после обновления 1C'}).first();await chat.waitFor();await chat.click();await page.locator('.case-results h3').filter({hasText:'2/2 шагов проверено'}).waitFor();
 if(!await page.locator('.phase-facts').evaluate(e=>e.open))await page.locator('.phase-facts>summary').click();
 await shot('10-live-main-verified');
 await page.getByRole('button',{name:'Устройства',exact:false}).filter({has:page.locator('b')}).click();await page.locator('tr').filter({hasText:'DEMO-VIRTUAL-WIN-01'}).waitFor();await shot('11-live-devices');
 await page.getByRole('button',{name:'Подключить устройство',exact:false}).click();await page.getByLabel('Тип устройства',{exact:true}).waitFor();assert.equal(await page.getByLabel('Тип устройства',{exact:true}).inputValue(),'unknown');await shot('18-live-onboarding');await page.getByRole('button',{name:'Закрыть',exact:true}).click();
 await page.locator('tr').filter({hasText:'DEMO-VIRTUAL-WIN-01'}).getByRole('button',{name:'Настроить',exact:true}).click();await page.getByLabel('Тип устройства',{exact:true}).waitFor();await shot('12-live-node-settings');
 for(const [name,file] of [['Настройка модели','13-live-model'],['События','14-live-events'],['Учётные записи','15-live-accounts'],['Знания','16-live-knowledge']]){
  await page.getByRole('button',{name,exact:name==='Настройка модели'||name==='Учётные записи'}).click();await page.waitForTimeout(300);await shot(file);
 }
 await page.getByRole('button',{name:'Основной раздел',exact:false}).click();await page.locator('.conversation-name').filter({hasText:'Проверка состояния после обновления 1C'}).first().click();await page.locator('.case-results h3').filter({hasText:'2/2 шагов проверено'}).waitFor();
 await page.setViewportSize({width:390,height:844});await shot('17-live-mobile');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 await page.setViewportSize({width:320,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 await page.evaluate(()=>{const sizes=Array.from(document.querySelectorAll('.case-workspace *')).map(e=>[e,parseFloat(getComputedStyle(e).fontSize)]);for(const [e,size] of sizes)e.style.fontSize=(size*2)+'px';});
 await shot('19-live-text-200');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
 fs.writeFileSync(path.join(root,'docs/qa/dialogue-case-workspace/reflow.json'),JSON.stringify({widths:[390,320],twoHundredPercentCaseText:true,testOnlyCSPBypassForTextResizing:true,documentOverflow:false,newNodeRoleUnknown:true,scope:'actual local UI, text enlargement not complete assistive technology audit'},null,2));console.log('Actual pages captured; 320/390 and 200% case text reflow passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
