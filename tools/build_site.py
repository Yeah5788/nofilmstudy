import json,glob,os,sys,re
src,dbdir,out=sys.argv[1],sys.argv[2],sys.argv[3]
os.makedirs(out,exist_ok=True)
data={}
for f in glob.glob(os.path.join(dbdir,'**','*.json'),recursive=True):
    rel=os.path.relpath(f,dbdir); parts=rel[:-5].split(os.sep)
    if parts[0]=='data': continue
    col='/'.join(parts[:-1]); d=json.load(open(f)); d=d.get('data',d) if isinstance(d,dict) and 'data' in d and 'version' in d else d
    data.setdefault(col,{})[parts[-1]]=d
json.dump(data,open(os.path.join(out,'data.json'),'w'),separators=(',',':'))
html=open(src).read()
shim=open(os.path.join(os.path.dirname(os.path.abspath(__file__)),'shim.js')).read()
hide='<style>#tabs a[data-r=slip],#tabs a[data-r=tracker],#tabs a[data-r=settings],.xlog,#who,.slipbar,.slipin{display:none!important}</style>'
banner='<div style="background:#111113;border-bottom:1px solid #26262b;color:#a1a1a6;font:12px IBM Plex Sans,sans-serif;text-align:center;padding:6px 12px">Live board · data refreshes hourly · picks are for information, not financial advice</div>'
page='<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'+hide+'<script>'+shim+'</script></head><body style="margin:0">'+banner+html+'</body></html>'
open(os.path.join(out,'index.html'),'w').write(page)
open(os.path.join(out,'.nojekyll'),'w').write('')
print('collections',{k:len(v) for k,v in data.items()},'bytes',os.path.getsize(os.path.join(out,'data.json')))
