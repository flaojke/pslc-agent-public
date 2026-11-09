const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),qa=JSON.parse(fs.readFileSync(path.join(root,'.local/compact-browser-qa.json'),'utf8')),out=path.join(root,'docs/qa/compact-historical-messages');fs.mkdirSync(path.join(out,'screenshots'),{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true}),page=await browser.newPage({viewport:{width:1440,height:1080}});page.setDefaultTimeout(15000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const api=async p=>{const r=await page.request.get(qa.url+'/api'+p);assert(r.ok());return r.json();};
 try{
 await page.goto(qa.url);await page.getByLabel('Логин',{exact:true}).fill('admin');await page.getByLabel('Пароль',{exact:true}).fill('admin');await page.getByRole('button',{name:'Войти в систему'}).click();await page.locator('.new-conversation').click();await page.getByRole('button',{name:'Переименовать',exact:true}).waitFor();
 async function send(q){await page.getByLabel('Сообщение агенту',{exact:true}).fill(q);await page.waitForFunction(()=>!document.querySelector('.case-composer button[type=submit]').disabled);await page.getByRole('button',{name:'Отправить →',exact:true}).click();}
 await send('QA-QUESTION-1\n'+'Длинное описание synthetic проблемы.\n'.repeat(20));await page.locator('.agent-message').filter({hasText:'QA-ANSWER-1'}).waitFor();
 assert.equal(await page.locator('.agent-message').first().locator('.message-toggle').count(),0);
 await send('Следующая проверка?');await page.locator('.agent-message').filter({hasText:'QA-ANSWER-2'}).waitFor();
 const first=page.locator('.agent-message').filter({hasText:'QA-ANSWER-1'}),second=page.locator('.agent-message').filter({hasText:'QA-ANSWER-2'});
 await first.locator('.is-collapsed').waitFor();assert((await first.locator('p').innerText()).endsWith('…'));assert(!(await first.innerText()).includes('QA-END-1'));
 assert.equal(await second.locator('.message-toggle').count(),0);assert((await second.innerText()).includes('QA-END-2'));
 const small=page.locator('.user-message').filter({hasText:'Следующая проверка?'});assert.equal(await small.locator('.message-toggle').count(),0);
 await first.getByRole('button',{name:'Развернуть сообщение агента'}).focus();await page.keyboard.press('Enter');assert((await first.innerText()).includes('QA-END-1'));
 await send('QA-QUESTION-3\n'+'Длинный ожидающий вопрос.\n'.repeat(20));const pending=page.locator('.user-message').filter({hasText:'QA-QUESTION-3'});await pending.waitFor();assert.equal(await pending.locator('.message-toggle').count(),0);assert((await pending.innerText()).includes('Длинный ожидающий вопрос.'));
 await second.locator('.is-collapsed').waitFor();assert((await first.innerText()).includes('QA-END-1'));
 await page.request.post(qa.url+'/qa/release-third');const last=page.locator('.agent-message').filter({hasText:'QA-ANSWER-3'});await last.waitFor();await pending.locator('.is-collapsed').waitFor();assert.equal(await last.locator('.message-toggle').count(),0);assert((await last.innerText()).includes('QA-END-3'));
 await first.getByRole('button',{name:'Свернуть сообщение агента'}).click();await first.locator('.is-collapsed').waitFor();
 await page.locator('.phase-clarify').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,'screenshots/01-compact-desktop.png')});
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);await first.getByRole('button',{name:'Развернуть сообщение агента'}).click();assert((await first.innerText()).includes('QA-END-1'));await first.getByRole('button',{name:'Свернуть сообщение агента'}).click();await page.screenshot({path:path.join(out,'screenshots/02-compact-mobile.png')});
 await page.setViewportSize({width:1440,height:1080});await page.getByLabel('Целевые машины',{exact:true}).selectOption('all');await page.getByLabel('Сообщение агенту',{exact:true}).fill('Проверь состояние всех машин');await page.waitForFunction(()=>!Array.from(document.querySelectorAll('button')).find(b=>b.textContent==='Подготовить план').disabled);await page.getByRole('button',{name:'Подготовить план',exact:true}).click();await page.getByRole('button',{name:'Подтвердить на 1 устройствах'}).waitFor();
 assert.equal((await api('/overview')).statistics.active_fleet,0);assert.equal(await page.locator('textarea').count(),1);assert.equal(await page.evaluate(()=>localStorage.length+sessionStorage.length),0);assert.deepEqual(errors,[]);
 const result={scope:'synthetic dialogue + unexecuted health plan',oldLongCollapsed:true,firstLineEllipsis:true,latestAlwaysExpanded:true,pendingQuestionExpanded:true,smallUnchanged:true,keyboardExpand:true,manualChoiceSurvivesAppend:true,canCollapseAgain:true,confirmControlUnaffected:true,oneComposer:true,mobile390NoOverflow:true,persistentBrowserStorage:false,pageErrors:errors};
 fs.writeFileSync(path.join(out,'browser.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }catch(e){await page.screenshot({path:path.join(out,'screenshots/failure.png')});throw e;}finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
