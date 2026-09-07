import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {fileURLToPath} from "node:url";
import test, {after} from "node:test";
import {createServer} from "vite";

const root = fileURLToPath(new URL("..", import.meta.url));
const vite = await createServer({configFile:false, appType:"custom", root,
  resolve:{alias:{"@":root}}, server:{middlewareMode:true, hmr:false}});
after(() => vite.close());
const {parseLayout} = await vite.ssrLoadModule("/app/layout-data.ts");
const {buildScene, buildSceneSteps} = await vite.ssrLoadModule("/app/layout-geometry.ts");
const {buildSpatialIndex, selectSceneDetail, runCooperatively, visibleCurveSamples} = await vite.ssrLoadModule("/app/layout-lod.ts");
const {buildSceneLayers} = await vite.ssrLoadModule("/app/layout-layers.ts");
const {cameraBoundsProjector, cameraProjector, fitCameraToPoints} = await vite.ssrLoadModule("/app/layout-viewport.tsx");
const {referenceCandidates, matchingNames} = await vite.ssrLoadModule("/app/layout-reference-options.ts");
const {editLayout} = await vite.ssrLoadModule("/app/layout-edit.ts");
const fixture = JSON.parse(await readFile(new URL("../../specifications/examples/anchored-features.json", import.meta.url),"utf8"));
const run = iterator => {let r; do {r = iterator.next();} while (!r.done); return r.value;};

test("deferred geometry preserves exact shapes and independently referenced feature poses", () => {
  const layout = parseLayout(fixture), full = buildScene(layout), lean = buildScene(layout,{kind:"layout"},{deferred:true});
  assert.equal(lean.objects.length,full.objects.length);
  assert.equal(lean.frames.length + lean.beamAxes.length + lean.magneticAxes.length,0);
  assert.equal(lean.deferred.cachedSolids(),0);
  for (const object of lean.objects) {
    const before = full.objects.find(o => o.name === object.name);
    const detailed = lean.deferred.detail(object);
    for (const key of ["frame","mechanicalFrame","vertices","faces","edges"]) assert.deepEqual(detailed[key],before[key],`${object.name}.${key}`);
    for (const point of detailed.vertices) point.forEach((v,i) => assert.ok(v >= object.bounds.min[i]-1e-10 && v <= object.bounds.max[i]+1e-10));
  }
  const layers = run(buildSceneLayers(lean,{frames:true,magnetic:true,beam:true}));
  for (const key of ["frames","magneticAxes","magneticFrames","beamAxes","beamFrames"]) {
    assert.deepEqual([...layers.byObject.values()].flatMap(layer => layer[key]), full[key]);
  }
  const external = full.frames.find(frame => frame.name === "survey_world");
  const bounds = layers.objects.find(o => o.name === external.object).bounds;
  external.frame.o.forEach((v,i) => assert.ok(v >= bounds.min[i] && v <= bounds.max[i]));
  assert.deepEqual(layout,parseLayout(fixture));
});

test("large scenes aggregate the overview, cull offscreen shapes, and retain selected detail", () => {
  const value = {reference_curves:{}, types:{box:{shape:["box",1,1,2,0,0],color:"#abcdef",frames:{}}},objects:{}};
  for (let i=0;i<5000;i++) value.objects[`B${i}`]={type:"box",position:{target:"anchor",reference:{kind:"world"},transformation:[["tx",(i%100)*10],["tt",Math.floor(i/100)*10]]}};
  const scene = buildScene(parseLayout(value),{kind:"layout"},{deferred:true});
  const index = run(buildSpatialIndex(scene.objects));
  const base = {azimuth:0,elevation:0.7,distance:1e6,target:[500,0,250]};
  const overview = selectSceneDetail(scene,index,cameraBoundsProjector(base,800,600));
  assert.ok(overview.proxies.length < 50);
  assert.equal(overview.proxies.reduce((n,p)=>n+p.count,0)+overview.objects.length,5000);
  const selected = selectSceneDetail(scene,index,cameraBoundsProjector(base,800,600),"B2000");
  assert.equal(selected.objects.filter(o=>o.name==="B2000").length,1);
  assert.equal(selected.proxies.reduce((n,p)=>n+p.count,0)+selected.objects.length,5000);
  const close = {...base,distance:12,target:scene.objects[2000].frame.o,axisScale:[0.3,4,1]};
  const detail = selectSceneDetail(scene,index,cameraBoundsProjector(close,800,600),"B2000");
  assert.ok(detail.objects.length < 100); assert.ok(detail.objects.some(o=>o.name==="B2000"));
  assert.ok(detail.visited < 500);
  for (const object of scene.objects) scene.deferred.detail(object);
  assert.equal(scene.deferred.cachedSolids(),2048);
});

test("bounds projection conservatively retains near-plane intersections under display scaling", () => {
  const camera={azimuth:0,elevation:0,distance:10,target:[0,0,0],axisScale:[0.01,2,1]};
  const project=cameraBoundsProjector(camera,800,600);
  assert.ok(project({min:[-100,-1,9],max:[100,1,11]}));
  assert.equal(project({min:[-1,-1,12],max:[1,1,13]}),null);
  assert.equal(project({min:[1e6,0,0],max:[1e6+1,1,1]}),null);
});

test("cooperative builds yield a curve preview and can be cancelled", async () => {
  const controller = new AbortController();
  const steps=buildSceneSteps(parseLayout(fixture),{kind:"layout"},{deferred:true});
  assert.ok(steps.next().value.preview.curves.length);
  controller.abort();
  await assert.rejects(runCooperatively(steps,controller.signal),{name:"AbortError"});
  assert.equal(steps.next().done,true);
});

test("reference candidate checks match frame graph semantics without filtering all objects", () => {
  const layout=parseLayout(fixture), candidates=referenceCandidates(layout,{kind:"object",name:"A"});
  assert.ok(!candidates.frames("A").includes("anchor"));
  assert.ok(candidates.frames("A").includes("survey_world"));
  const names=Array.from({length:100000},(_,i)=>`magnet-${i}`);
  let checked=0;
  assert.equal(matchingNames(names,"",()=>{checked++;return true;}).length,50);
  assert.equal(checked,50);
  assert.deepEqual(matchingNames(names,"magnet-99999"),["magnet-99999"]);
});

test("adaptive reference curve drawing refines bends at close range", () => {
  const layout=parseLayout({reference_curves:{arc:{color:"#abcdef",starting_frame:{reference:{kind:"world"},transformation:[]},segments:[[10,Math.PI,0]]}},types:{},objects:{}});
  const curve=buildScene(layout,{kind:"layout"},{deferred:true}).curves[0];
  const camera={azimuth:0,elevation:Math.PI/2,distance:12,target:[-3,0,0]};
  const points=visibleCurveSamples(curve,cameraProjector(camera,800,600),800,600).filter(Boolean);
  assert.ok(points.length>16);
  assert.ok(points[0].path === 0 && Math.abs(points.at(-1).path-10)<1e-10);
});

test("editing copies changed branches, supports rename and array operations, and emits cloneable JSON", () => {
  const base = parseLayout(fixture);
  const edit = editLayout(base,draft => {draft.objects.A.position.transformation.push(["tx",1]);});
  assert.notEqual(edit.objects.A,base.objects.A);
  assert.equal(edit.objects.B,base.objects.B);
  assert.equal(edit.types,base.types);
  assert.notDeepEqual(edit.objects.A.position.transformation,base.objects.A.position.transformation);
  assert.doesNotThrow(()=>structuredClone(edit));
  const renamed = editLayout(base,draft => {
    draft.objects = Object.fromEntries(Object.entries(draft.objects).map(([key,value])=>[key === "A" ? "renamed" : key,value]));
    draft.objects.renamed.position.transformation.splice(0,1);
    delete draft.objects.B;
  });
  assert.ok(!renamed.objects.A && !renamed.objects.B && renamed.objects.renamed);
  assert.deepEqual(base,parseLayout(fixture));
  assert.doesNotThrow(()=>structuredClone(renamed));
});
