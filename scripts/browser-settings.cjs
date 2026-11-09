const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),qa=JSON.parse(fs.readFileSync(path.join(root,'.local/settings-browser-qa.json'),'utf8')),out=path.join(root,'docs/qa/settings-guides');fs.mkdirSync(path.join(out,'screenshots'),{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1080}});page.setDefaultTimeout(18000);
 const errors=[],actions=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(r.method()!=='GET')actions.push(new URL(r.url()).pathname);});
 const login=async(name='admin',password='admin')=>{await page.goto(qa.url);await page.getByLabel('Логин',{exact:true}).fill(name);await page.getByLabel('Пароль',{exact:true}).fill(password);await page.getByRole('button',{name:'Войти в систему'}).click();await page.getByRole('button',{name:'Настройки',exact:true}).waitFor();};
 const get=async(url)=>{const r=await page.request.get(qa.url+url);assert(r.ok());return r.json();};
 const open=async(name)=>{await page.locator('.settings-nav').getByRole('button',{name,exact:true}).click();await page.getByRole('heading',{name,exact:true}).waitFor();};
 const shot=async(name)=>page.screenshot({path:path.join(out,'screenshots',name)});
 try{
 await login();await page.locator('.new-conversation').click();await page.getByRole('button',{name:'Переименовать',exact:true}).waitFor();const chat=await get('/api/conversations');assert(chat.length>=1);
 const before=await get('/api/settings/model');await page.getByRole('button',{name:'Настройки',exact:true}).click();await page.locator('.settings-nav').waitFor();assert.equal(await page.locator('.settings-nav button').count(),7);
 await page.getByRole('button',{name:'Инструкция подключения LLM',exact:true}).click();await page.locator('.product-guide').getByRole('heading',{name:'Подключение LLM: локальная модель и OpenWebUI',exact:true}).waitFor();assert((await page.locator('.product-guide').innerText()).includes('Загрузить модели'));
 await shot('01-model-guide.png');
 const actionsBeforeHelp=actions.length;
 for(const name of ['Инструкция пользователя','Инструкция администратора','Инструкция развёртывания']){
  await open(name);await page.locator('.product-guide section').first().waitFor();assert((await page.locator('.product-guide').innerText()).length>5000);
  assert.equal(await page.locator('.product-guide script').count(),0);
  const link=page.locator('.guide-toc a').first();await link.focus();await page.keyboard.press('Enter');
  if(name==='Инструкция развёртывания')await shot('02-deployment-guide.png');
 }
 assert.equal(actions.length,actionsBeforeHelp);
 assert.equal((await get('/qa/status')).provider_calls,0);
 // Authored strings remain text even if a compromised guide response contains markup.
 await page.route('**/api/help/user',async route=>{const response=await route.fetch(),body=await response.json();body.sections[0].blocks=[{type:'paragraph',text:'<img src=x onerror="window.badGuide=true">SAFE-TEXT-FIXTURE'}];await route.fulfill({response,json:body});});
 await open('Инструкция пользователя');await page.getByText('<img src=x onerror="window.badGuide=true">SAFE-TEXT-FIXTURE',{exact:true}).waitFor();assert.equal(await page.locator('.product-guide img').count(),0);assert.equal(await page.evaluate(()=>window.badGuide),undefined);await page.unroute('**/api/help/user');
 await open('Настройки системы');await page.getByLabel('Метка контура',{exact:true}).fill('QA · изолированный пилот');await page.getByLabel('Контакт поддержки',{exact:true}).fill('support@example.invalid');await page.getByLabel('Часовой пояс интерфейса (IANA)',{exact:true}).fill('UTC');await page.getByRole('button',{name:'Сохранить настройки',exact:true}).click();await page.getByRole('status').filter({hasText:'настройки сохранены'}).waitFor();
 assert.equal((await get('/api/settings/system')).display_timezone,'UTC');await shot('03-system-settings.png');
 await open('О системе');await page.getByText('API 0.15.0',{exact:true}).waitFor();await page.getByText('Web 0.15.0',{exact:true}).waitFor();assert((await page.locator('.feature-list').innerText()).includes('Следующий срез'));await shot('04-about.png');
 await open('Доступ и роли');await page.locator('.role-cards article').first().waitFor();assert.equal(await page.locator('.role-cards article').count(),4);
 await page.getByText('Добавить сотрудника',{exact:true}).click();const createForm=page.locator('details').filter({has:page.getByText('Добавить сотрудника',{exact:true})}).locator('form');assert.equal(await createForm.locator('.role-presets button').count(),4);await createForm.locator('.role-presets').getByRole('button',{name:'Аудитор ИБ',exact:true}).click();assert.equal(await createForm.getByLabel('Все узлы',{exact:true}).isChecked(),false);await shot('05-access-roles.png');
 await page.setViewportSize({width:390,height:844});await open('Инструкция развёртывания');await page.locator('.product-guide section').first().waitFor();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await shot('06-guide-mobile.png');
 await page.setViewportSize({width:1440,height:1080});await open('Модели');const after=await get('/api/settings/model');assert.equal(after.revision,before.revision);assert.equal(after.model_id,before.model_id);
 await page.getByRole('button',{name:/Основной раздел/}).click();await page.getByRole('button',{name:'Переименовать',exact:true}).waitFor();assert.equal((await get('/api/conversations')).length,chat.length);
 for(const role of ['operator','auditor','reviewer','platform_admin']){
  await page.getByRole('button',{name:'Завершить сеанс',exact:true}).click();await login('qa-'+role,'public-browser-fixture');await page.getByRole('button',{name:'Настройки',exact:true}).click();await page.locator('.product-guide section').first().waitFor();await open('Настройки системы');assert(await page.getByLabel('Метка контура',{exact:true}).isDisabled());assert.equal(await page.getByRole('button',{name:'Сохранить настройки',exact:true}).count(),0);await open('Доступ и роли');assert.equal(await page.getByText('Добавить сотрудника',{exact:true}).count(),0);assert.equal(await page.getByRole('heading',{name:'Мой пароль',exact:true}).count(),1);
 }
 assert.deepEqual(errors,[]);assert.equal((await get('/qa/status')).provider_calls,0);assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);
 const result={scope:'synthetic isolated settings UI',checks:['7 sections','ModelHub profile unchanged','guides offline/no actions','safe text renderer','role presets explicit scope','all four roles deny global edits without global scope','last conversation preserved','display timezone','390px layout','keyboard navigation','no storage/secrets'],page_errors:errors,provider_calls:0,real_host_actions:false};fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
