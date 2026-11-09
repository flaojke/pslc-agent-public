const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PSLC_PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const out=path.resolve(__dirname,'../docs/qa/model-release'),browser=await chromium.launch({channel:'msedge',headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:8794');assert.equal((await(await page.request.get('http://127.0.0.1:8794/healthz')).json()).version,'0.11.0');
  await page.locator('#username').fill('admin');await page.locator('#password').fill('admin');await page.getByRole('button',{name:'Войти в систему'}).click();
  await page.getByRole('heading',{name:'Основной раздел',exact:true}).waitFor();assert.equal(await page.locator('textarea').count(),1);
  assert.deepEqual(await(await page.request.get('http://127.0.0.1:8794/api/devices')).json(),[]);
  await page.getByRole('button',{name:'Настройка модели',exact:true}).click();
  await page.getByLabel('Адрес сервера Open WebUI',{exact:true}).waitFor();assert.equal(await page.getByLabel('API key Open WebUI',{exact:true}).inputValue(),'');
  const config=await(await page.request.get('http://127.0.0.1:8794/api/settings/model')).json();assert(!config.has_key&&config.status==='not_configured');
  await page.screenshot({path:path.join(out,'screenshots/09-clean-release.png'),fullPage:true});assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'clean-release.json'),JSON.stringify({version:'0.11.0',scope:'clean extracted Windows source ZIP with existing pinned Python interpreter',freshDependencyInstallationTested:false,importsFromExtractedArtifact:true,compiledFrontendServed:true,adminBootstrapLogin:true,emptyInventory:true,modelSettingsAccessible:true,noBundledKey:true,pageErrors:errors},null,2));
  console.log('Clean extracted release browser passed.');
 }finally{await browser.close();}
})().catch(e=>{console.error('Extracted release check failed: '+e.message.split('Call log:')[0]);process.exit(1)});
