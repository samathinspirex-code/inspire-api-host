"""Package the same Payments UI used by the CMS; no demo engine or sample data."""
import base64
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / 'CMS-UI' / 'src' / 'redesign' / 'payments'
OUT = ROOT / 'outputs' / 'payments-sandbox-console'
OUT.mkdir(parents=True, exist_ok=True)

def text(path):
    return path.read_text(encoding='utf-8')

styles = '\n'.join(text(UI / p) for p in ['demo/brain-base.css', 'demo/acc.css', 'embed.css'])
for family, file in [('Geist','geist-latin.woff2'),('Geist Mono','geist-mono-latin.woff2')]:
    encoded = base64.b64encode((UI/'fonts'/file).read_bytes()).decode()
    styles += f"\n@font-face{{font-family:'{family}';src:url(data:font/woff2;base64,{encoded}) format('woff2');font-weight:100 900}}"
sources = [ROOT/'CMS-UI/node_modules/chart.js/dist/chart.umd.js', UI/'demo/accounting-core.js', UI/'live-api.js', UI/'demo/acc.js']
channel = '__PAYMENTS_CHANNEL__'
frame = '<!doctype html><html data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
frame += '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; font-src data:; img-src data: blob:; connect-src \'none\'">'
frame += '<style>'+styles+'</style></head><body><div id="acc"></div>'
for path in sources:
    frame += '<script>'+text(path).replace('</script','<\\/script')+'</script>'
frame += '<script>const api=IBLiveApi.create({channel:'+json.dumps(channel)+'});IBAccounts.mount(document.getElementById("acc"),api,{tours:false}).show();</script></body></html>'
html = '''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Payments sandbox console</title><style>body{margin:0;font:14px system-ui;background:#f5f5f7}header{padding:12px 20px;background:#fff0bf;color:#352800}iframe{display:block;width:100%;height:calc(100dvh - 64px);border:0}</style></head><body><header><b>PAYMENTS SANDBOX</b> — Separate test ledger · PayHere sandbox · Messages disabled<br>Manual receipts require confirmation. Use fictional learners only.</header><iframe id="console" title="Sandbox payments" sandbox="allow-scripts allow-downloads allow-forms allow-modals"></iframe><script>
const frame=document.getElementById('console');
frame.srcdoc=__FRAME__;
__REQUEST_POLICY__
window.addEventListener('message',async event=>{
 const m=event.data;
 if(event.source!==frame.contentWindow||m?.type!=='cms-payments-request'||m.channel!=='__PAYMENTS_CHANNEL__')return;
 if(typeof m.id!=='string'||!isPaymentPath(m.path)||m.path.startsWith('/sample-data')||!['GET','POST','PUT','PATCH'].includes(m.method))return;
 try{
  const headers={'X-Sandbox-CSRF':'__SANDBOX_CSRF__'};
  if(m.method!=='GET')headers['Idempotency-Key']=m.id;
  if(m.method==='POST'&&m.path==='/receipts')headers['X-Sandbox-Receipt-Confirmation']='confirmed';
  if(m.body!==undefined)headers['Content-Type']=m.raw?'text/csv':'application/json';
  const response=await fetch('/sandbox-payments/api/v1/cms/payments'+m.path,{method:m.method,credentials:'same-origin',headers,body:m.body===undefined?undefined:m.raw?String(m.body):JSON.stringify(m.body)});
  const result=await response.json().catch(()=>null);
  if(!response.ok)throw new Error(result?.error?.message||result?.detail||'Request failed');
  frame.contentWindow.postMessage({type:'cms-payments-response',channel:m.channel,id:m.id,result},'*');
 }catch(error){frame.contentWindow.postMessage({type:'cms-payments-response',channel:m.channel,id:m.id,error:error.message},'*');}
});
</script></body></html>'''
html = html.replace('__FRAME__', json.dumps(frame).replace('</','<\\/'))
html = html.replace('__REQUEST_POLICY__', text(UI/'request-policy.js').replace('export function ', 'function '))
(OUT/'sandbox-console.html').write_text(html,encoding='utf-8')
for name in ['payments_sandbox.py','sandbox_console.py','configure_sandbox_console.py']:
    (OUT/name).write_text(text(Path(__file__).with_name(name)),encoding='utf-8')
print('Sandbox console package created:', OUT)
