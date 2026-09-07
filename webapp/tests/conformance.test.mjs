import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test, {after} from 'node:test';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import Ajv2020 from 'ajv/dist/2020.js';

const root = fileURLToPath(new URL('..', import.meta.url));
const vite = await createServer({configFile:false, appType:'custom', root,
  server:{middlewareMode:true, hmr:false}});
after(()=>vite.close());
const {parseLayout, objectFrameNames} = await vite.ssrLoadModule('/app/layout-data.ts');
const {parseLayoutJson} = await vite.ssrLoadModule('/app/layout-json.ts');
const {buildScene, frameAtCurvePath, closestTransverseCurvePathForPoint} = await vite.ssrLoadModule('/app/layout-geometry.ts');
const corpus = JSON.parse(await readFile(new URL('../../specifications/conformance/cases.json', import.meta.url),'utf8'));
const schema = JSON.parse(await readFile(new URL('../../specifications/layout.schema.json', import.meta.url),'utf8'));
const validateSchema = new Ajv2020({strict:false, allErrors:true}).compile(schema);
const matrix = f => [0,1,2].map(i=>[f.x[i],f.y[i],f.s[i],f.o[i]]).concat([[0,0,0,1]]);
const close = (actual, expected) => actual.flat().forEach((v,i)=> {
  const target = expected.flat()[i];
  assert.ok(Math.abs(v-target) <= corpus.matrix_atol + corpus.matrix_rtol*Math.abs(target), `${v} != ${target}`);
});

function check(input) {
  if(input.layout) assert.equal(validateSchema(input.layout), input.schema_valid, JSON.stringify(validateSchema.errors));
  const parse = ()=>parseLayout(input.json_text ? parseLayoutJson(input.json_text) : input.layout);
  if(input.error?.stage === 'parse') { assert.throws(parse); return; }
  const layout = parse();
  let scene;
  const solve = ()=> {
    scene = buildScene(layout, {kind:'layout'}, {deferred:true});
    for(const [name, object] of Object.entries(layout.objects)) {
      for(const frame of objectFrameNames(layout.types[object.type],object)) scene.deferred.resolveFrame(name,frame);
    }
  };
  if(input.error) {
    const pattern = {ambiguous:/equally close/, no_station:/no transverse plane/, numeric_range:/numeric range/}[input.error.category];
    assert.throws(solve, pattern); return;
  }
  solve();
  for(const q of input.frames ?? []) close(matrix(scene.deferred.resolveFrame(q.object,q.frame)),q.matrix);
  for(const q of input.curve_frames ?? []) close(matrix(frameAtCurvePath(scene.curves.find(c=>c.name===q.curve),q.s)),q.matrix);
  for(const q of input.stations ?? []) {
    const actual = closestTransverseCurvePathForPoint(scene.curves.find(c=>c.name===q.curve),q.point);
    assert.equal(actual.kind, q.expected.kind==='ambiguous' ? 'equidistant' : 'unique');
    if(q.expected.kind==='unique') assert.ok(Math.abs(actual.path-q.expected.s)<=1e-10);
  }
}
for(const input of corpus.cases) test('conformance: '+input.id, ()=>check(input));
test('dictionary order does not change the solution', ()=> {
  const reverse = v => Array.isArray(v) ? v.map(reverse) : v && typeof v==='object'
    ? Object.fromEntries(Object.entries(v).reverse().map(([k,x])=>[k,reverse(x)])) : v;
  const input=corpus.cases.find(c=>c.id==='anchored-features');
  check({...input,layout:reverse(input.layout)});
});
test('the evaluator rejects missing references and cycles even without a prior parser', ()=> {
  for(const reference of [{kind:'curve',curve:'missing'}, {kind:'object_frame',object:'missing',frame:'anchor'}]) {
    const layout=structuredClone(corpus.cases.find(c=>c.id==='small-angle').layout);
    layout.reference_curves.main.starting_frame.reference=reference;
    assert.throws(()=>buildScene(layout), /Unknown/);
  }
  const layout=structuredClone(corpus.cases.find(c=>c.id==='small-angle').layout);
  layout.reference_curves.main.starting_frame.reference={kind:'curve',curve:'main'};
  assert.throws(()=>buildScene(layout), /cycle/);
});
