import {workspaceFilePath, fileLanguage} from '../static/js/workspaceFileLinks.js';
function equal(actual, expected) {if(actual!==expected)throw new Error(JSON.stringify({actual,expected}));}
Deno.test('workspace links preserve local identity and decode safely', () => {
  for(const path of ['test.txt','folder/readme.md','../test.txt','/workspace/test.txt','a file.txt'])equal(workspaceFilePath(path),path);
  equal(workspaceFilePath('folder/a%20file.txt#L2'),'folder/a file.txt');
  equal(workspaceFilePath('test.txt?download=1'),'test.txt');
});
Deno.test('schemes, anchors, private endpoints and malformed controls stay out', () => {
  for(const path of ['#document-123','https://example.com/a.txt','http://example.com','//example.com/file','javascript:evil','data:text/plain,hi','file:///tmp/a.txt','%6aavascript%3aevil','%2f%2fevil','%00a.txt','a\n.txt','%xx','/api/upload/a.txt','/static/a.js'])equal(workspaceFilePath(path),null);
});
Deno.test('inline code only promotes filename-shaped content', () => {
  equal(workspaceFilePath('test.txt',{inline:true}),'test.txt');
  equal(workspaceFilePath('file%20name.txt',{inline:true}),'file%20name.txt');
  for(const path of ['x=1','some prose','x < y.txt','*.txt','a\nb.txt'])equal(workspaceFilePath(path,{inline:true}),null);
});
Deno.test('document copies use native language labels', () => {
  equal(fileLanguage('readme.md'),'markdown');equal(fileLanguage('sample.py'),'python');equal(fileLanguage('test.txt'),'text');
});
