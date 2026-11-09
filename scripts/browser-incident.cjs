const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),fixture=JSON.parse(fs.readFileSync(path.join(root,'.local/incident-browser-qa.json'),'utf8'));
 const out=path.join(root,'docs/qa/incident-investigation/screenshots');fs.mkdirSync(out,{recursive:true});
 const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
  const login=async()=>{await page.locator('#token').fill(fixture.token);await page.getByRole('button',{name:'Открыть рабочее место'}).click();await page.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();await page.getByRole('button',{name:'Проверить incident-qa-fixture',exact:true}).waitFor();};
  const api=async(endpoint,method='GET',body)=>{const r=await page.request.fetch(fixture.url+endpoint,{method,headers:{Authorization:'Bearer '+fixture.token},...(body?{data:body}:{})});if(!r.ok())throw new Error('Fixture API '+endpoint+' returned '+r.status());return r.json();};
  const status=()=>page.getByTestId('workflow-status');
  const prepare=async()=>{await page.getByLabel('Задание агенту',{exact:true}).fill('Расследуй ошибки на incident-qa-fixture за последние 60 минут');await page.getByRole('button',{name:'Подготовить план'}).click();await status().filter({hasText:'Нужно ваше подтверждение'}).waitFor();await page.getByText('Область расследования',{exact:true}).waitFor();};
  const approve=async()=>page.getByRole('button',{name:'Подтвердить весь план',exact:true}).click();
  await page.goto(fixture.url);await login();assert.equal(await page.locator('textarea').count(),1);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'00-main.png'),fullPage:true});
  await prepare();assert.equal((await api('/api/overview')).statistics.active_fleet,0);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'01-scope-plan.png'),fullPage:true});
  await page.getByRole('button',{name:'Отклонить план',exact:true}).click();await status().filter({hasText:'План отклонён'}).waitFor();
  await prepare();await approve();await status().filter({hasText:'Сбор данных проверен'}).waitFor({timeout:15000});
  await page.getByTestId('analysis-status').filter({hasText:'Гипотезы подготовлены'}).waitFor();await page.getByText('Причина пока не установлена',{exact:true}).waitFor();
  await page.locator('.hypothesis').getByText('QA fixture: совпадение времени не доказывает причину. '+fixture.marker,{exact:true}).waitFor();
  await page.locator('.evidence-list summary').click();await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'02-evidence-hypotheses.png'),fullPage:true});
  const rows=await api('/api/workflows');assert(!JSON.stringify(rows).includes(fixture.marker));assert(!fs.readFileSync(path.join(fixture.directory,'qa.sqlite3')).includes(Buffer.from(fixture.marker)));
  await page.reload();await login();await page.getByTestId('analysis-status').filter({hasText:'Гипотезы подготовлены'}).waitFor();
  assert.equal(await page.getByText(fixture.marker,{exact:false}).count(),0);assert.equal(await page.locator('.hypothesis p').count(),0);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'03-reload-metadata.png'),fullPage:true});
  await api('/qa/mode','POST',{mode:'partial'});await prepare();await approve();await status().filter({hasText:'Цепочка остановлена'}).waitFor({timeout:15000});await page.getByText('Нет доступа',{exact:true}).waitFor();await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'04-partial-coverage.png'),fullPage:true});
  await api('/qa/mode','POST',{mode:'invalid'});await prepare();await approve();await page.getByTestId('analysis-status').filter({hasText:'Ответ модели не прошёл проверку'}).waitFor({timeout:15000});await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'05-invalid-analysis.png'),fullPage:true});
  await api('/qa/mode','POST',{mode:'delay'});await prepare();await approve();await page.getByRole('button',{name:'Остановить анализ',exact:true}).waitFor({timeout:10000});await page.getByRole('button',{name:'Остановить анализ',exact:true}).click();await page.getByTestId('analysis-status').filter({hasText:'Анализ остановлен'}).waitFor();
  await page.waitForTimeout(8500);await page.getByTestId('analysis-status').filter({hasText:'Анализ остановлен'}).waitFor();assert.equal(await page.locator('.hypothesis p').count(),0);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'06-cancelled-analysis.png'),fullPage:true});
  await api('/qa/mode','POST',{mode:'normal'});await prepare();await approve();await page.getByTestId('analysis-status').filter({hasText:'Гипотезы подготовлены'}).waitFor({timeout:15000});
  await page.setViewportSize({width:390,height:844});assert.equal(await page.locator('textarea').count(),1);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await page.evaluate(()=>window.scrollTo(0,0));await page.screenshot({path:path.join(out,'07-mobile-investigation.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(root,'docs/qa/incident-investigation/browser.json'),JSON.stringify({isolatedFixture:true,realLLM:false,realSystemLogsInFixture:false,onePrompt:true,scopeReviewed:true,noExecutionBeforeConfirmation:true,rejection:true,evidenceMetadata:true,hypothesesUnconfirmed:true,reloadMetadataOnly:true,partialCoverage:true,invalidModelRefsBlocked:true,cancelSuppressesLateResponse:true,rawMarkerAbsentDatabase:true,mobileHorizontalOverflow:false,pageErrors:errors,screenshots:8},null,2));
  console.log('Incident browser QA passed: scope/confirmation, evidence/hypotheses, partial coverage, reload, invalid model refs, cancellation and mobile.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
