"""artifact 화면(index.html)을 사내 서버용 public/index.html로 만든다."""
import json, sys, os
src=open(sys.argv[1],encoding="utf-8").read()
cdn='<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>'
assert src.count(cdn)==1
here=os.path.dirname(os.path.abspath(__file__))
if "--inline" in sys.argv:
    rd=lambda p:open(os.path.join(here,"public",p),encoding="utf-8").read()
    x,sh=rd("vendor/xlsx.full.min.js"),rd("shim.js")
    assert "</script" not in x.lower() and "</script" not in sh.lower()
    src=src.replace(cdn,"<script>"+x+"</script>\n<script>"+sh+"</script>")
else:
    src=src.replace(cdn,'<script src="vendor/xlsx.full.min.js"></script>\n<script src="shim.js"></script>')
head='''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-capable" content="yes"><meta name="apple-mobile-web-app-title" content="수율 일지"><style>:root{color-scheme:light;box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;padding:0;font:14px -apple-system,BlinkMacSystemFont,"Malgun Gothic",sans-serif}img{max-width:100%}[hidden]{display:none!important}</style></head><body>
'''
out=head+src+'\n</body></html>\n'
dst=sys.argv[sys.argv.index("--out")+1] if "--out" in sys.argv else os.path.join(here,"public","index.html")
open(dst,"w",encoding="utf-8").write(out)
print("built", len(out))
