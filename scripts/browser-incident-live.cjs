const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const root=path.resolve(__dirname,'..'),settings=JSON.parse(fs.readFileSync(path.join(root,'.local/settings.json'),'utf8'));
 const identity=JSON.parse(fs.readFileSync(path.join(root,'.local/smoke-agent.json'),'utf8'));
 const previous=JSON.parse(fs.readFileSync(path.join(root,'.local/pre-incident-device-ids.json'),'utf8'));
 const out=path.join(root,'docs/qa/incident-investigation/screenshots');
 const browser=await chromium.launch({channel:'msedge',headless:true});const errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});page.on('pageerror',e=>errors.push(e.message));
  const request=await browser.newContext();const get=async endpoint=>{const r=await request.request.get('http://127.0.0.1:8787'+endpoint,{headers:{Authorization:'Bearer '+settings.operator_token}});assert(r.ok());return r.json();};
  assert.equal((await get('/healthz')).version,'0.4.0');
  const devices=await get('/api/devices');assert.deepEqual(devices.map(d=>d.id).sort(),previous.sort());
  const node=devices.find(d=>d.id===identity.device_id);assert(node&&!node.revoked&&!node.commands_enabled&&!node.installations_enabled&&!node.diagnostics_enabled);
  const gate=await request.request.post('http://127.0.0.1:8787/api/agent/requests',{headers:{Authorization:'Bearer '+settings.operator_token},data:{request_id:require('node:crypto').randomUUID(),session_id:'live-incident-gate',question:'Расследуй ошибки на '+node.id+' за последние 60 минут'}});
  assert.equal(gate.status(),403);assert.equal((await gate.json()).error,'diagnostics_disabled');
  await page.goto('http://127.0.0.1:8787');await page.locator('#token').fill(settings.operator_token);await page.getByRole('button',{name:'Открыть рабочее место'}).click();
  await page.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();assert.equal(await page.locator('textarea').count(),1);
  await page.getByLabel('Задание агенту',{exact:true}).fill('Проверь состояние узла '+node.id);
  await page.getByRole('button',{name:'Подготовить план'}).click();await page.getByTestId('workflow-status').filter({hasText:'Нужно ваше подтверждение'}).waitFor();
  await page.screenshot({path:path.join(out,'09-live-plan.png'),fullPage:true});
  await page.getByRole('button',{name:'Подтвердить весь план',exact:true}).click();
  await page.getByTestId('workflow-status').filter({hasText:'Результат проверен'}).waitFor({timeout:20000});
  await page.getByText('CPU, %',{exact:true}).waitFor();await page.screenshot({path:path.join(out,'10-live-health-verified.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(root,'docs/qa/incident-investigation/live-health.json'),JSON.stringify({version:'0.4.0',realLoopbackWindowsAgent:true,sameIdentity:true,allDeviceIdsPreserved:true,operatorCredentialUnchanged:true,installationCapabilityRemainsDisabled:true,commandsCapabilityRemainsDisabled:true,diagnosticCapabilityRemainsDisabled:true,diagnosticRequestRejected:true,requestThroughMainSection:true,explicitWholePlanConfirmation:true,typedHealthVerified:true,realModel:false,noMutatingCommand:true,pageErrors:errors},null,2));
  console.log('Live 0.4.0 health cycle passed; diagnostic request denied, identity/token/capabilities preserved.');
 }finally{await browser.close();}
})().catch(e=>{console.error(e.message);process.exit(1)});
