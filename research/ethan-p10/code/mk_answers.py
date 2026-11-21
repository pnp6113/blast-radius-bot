import json,sys
rows=[json.loads(l) for l in open(sys.argv[1])]
json.dump({'commits':{r['mutant'][:-6]:{'failing':r['fail'],'kind':'code'} for r in rows}},open(sys.argv[2],'w'))
print(len(rows), sum(bool(r['fail']) for r in rows))
