"""Build TS/TT teaching cases with Layout Studio's exact frame resolver.

Usage: python build_cases.py --repo /path/to/layout_studio
Matplotlib only lays out the scientific plots. Every geometry point and frame
comes from Layout Studio. No tracking or field computation is implied.
"""
import argparse, copy, gzip, json, math, sys
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--repo', type=Path, required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.repo/'python_api/src'))
from layout_studio import Layout
from layout_studio.resolver import Resolver

C = dict(ts='#176BBD', tt='#D55325', offset='#168773', equal='#8B4DA8', body='#BCC8D5', ref='#66788A')
plt.rcParams.update({'font.family':'DejaVu Sans','font.size':12,'axes.spines.top':False,'axes.spines.right':False})
REPORT = {'source_commit':'415b051e7ca97625ef920f587dbbd0c108a8422a','cases':{},'checks':[]}
DATA = {}

def world(): return {'kind':'world'}
def curve(name): return {'kind':'curve','curve':name}
def obj(name, frame='anchor'): return {'kind':'object_frame','object':name,'frame':frame}
def placement(ref=None, ops=()):
    p={'transformation':list(ops)}
    if ref is not None: p['reference']=ref
    return p
def pos(ref,ops=(),target='anchor',refcurve=None):
    p={'target':target,'reference':ref,'transformation':list(ops)}
    if refcurve: p['reference_curve']=refcurve
    return p
def empty(): return {'reference_curves':{},'types':{},'objects':{}}
def addcurve(d,name,segs,ref=None,ops=(),color=None):
    d['reference_curves'][name]={'color':color or C['ts'],'starting_frame':placement(ref or world(),ops),'segments':segs}
def marker(d,name,p,color,size=.06):
    typ='marker_'+name
    d['types'][typ]={'color':color,'shape':['box',size*1.4,size*1.4,size*2,0,0],'frames':{}}
    d['objects'][name]={'type':typ,'position':p}
def magnet(d,name,L,k,width=.5,position=None,magk=None,beam=None):
    t={'color':C['body'],'shape':['box',width,.35,L,k,0],'frames':{},
       'magnetic_center':placement(),'magnetic_length':L,'magnetic_curvature':k if magk is None else magk,'magnetic_roll':0}
    d['types'][name]=t
    d['objects'][name]={'type':name,'position':position or pos(world())}
    if beam: d['objects'][name].update(beam)
def endpoints(d,path,L,offset=0,size=.06):
    marker(d,'A',pos(curve(path),[['tx',offset]]),'#263749',size)
    marker(d,'TS',pos(curve(path),[['ts',L],['tx',offset]]),C['ts'],size)
    marker(d,'TT',pos(obj('A'),[['tt',L]]),C['tt'],size)
def length(d,path): return sum(v[0] for v in d['reference_curves'][path]['segments'])

def save(name,d,meta):
    lay=Layout.from_dict(d)
    lay.validate()
    # Resolve all frames, including hidden ones, and verify exact JSON round-trip.
    poses={}
    for n,o in lay.objects.items():
        poses[n]=o.get_frame().matrix.tolist()
        for f in o.type.frames: o.get_frame(f)
        if o.type.shape:
            for f in ['mechanical_entry','mechanical_center','mechanical_exit']:o.get_frame(f)
        if o.type.magnetic_length is not None:
            for f in ['magnetic_entry','magnetic_center','magnetic_exit','beam_entry','beam_center','beam_exit']:o.get_frame(f)
    for c in lay.curves.values():c.get_frame(0)
    file=ROOT/'cases'/f'{name}.json'
    lay.to_json(str(file))
    other=Layout.from_json(str(file))
    for n,o in other.objects.items(): np.testing.assert_allclose(o.get_frame().matrix,poses[n],atol=1e-12,rtol=0)
    if 'TS' in lay.objects and 'TT' in lay.objects:
        a,b=lay.objects['TS'].get_frame(),lay.objects['TT'].get_frame()
        meta['ts_tt_distance_m']=float(np.linalg.norm(a.origin-b.origin))
        meta['ts_tt_angle_rad']=float(math.acos(np.clip(a.tangent@b.tangent,-1,1)))
    REPORT['cases'][name]=meta|{'objects':len(lay.objects),'curves':len(lay.curves),'poses':poses}
    DATA[name]=(lay,d,meta)
    return lay

def sample(lay,path,s=None,x=0,y=0):
    if s is None:
        segs=lay.curves[path].to_dict()['segments']; breaks=np.r_[0,np.cumsum([p[0] for p in segs])]
        s=np.unique(np.r_[np.linspace(0,breaks[-1],500),breaks])
    resolver=Resolver(lay)
    frames=[resolver.curve_frame(lay.curves[path],float(v)) for v in s]
    return np.array([f.origin+x*f.x+y*f.y for f in frames])
def coord(p): return np.asarray(p)[...,[2,0]]
def axis_plot(ax,pts,label,color,lw=2.4,style='-'):
    q=coord(pts); ax.plot(q[:,0],q[:,1],style,c=color,lw=lw,label=label)
def markplot(ax,lay,n,label=None,shift=(8,10),scale=.28):
    f=lay.objects[n].get_frame(); p=coord(f.origin); t=coord(f.tangent)
    color=lay.objects[n].type.color
    ax.scatter(*p,s=65,color=color,zorder=5,edgecolor='white',linewidth=.8)
    ax.annotate('',xy=p+scale*t,xytext=p,arrowprops={'arrowstyle':'->','color':color,'lw':1.7})
    if label:ax.annotate(label,p,xytext=shift,textcoords='offset points',color=color,fontweight='bold',fontsize=11)
def bodies(ax,lay):
    for name,o in lay.objects.items():
        if name in ['M','SB','RB'] or name.startswith('E'):
            raw=o.type.to_dict()['shape']; w,L,k=raw[1],raw[3],raw[4]
            f=o.get_frame('mechanical_center')
            # Evaluate the mechanical path using a separate Layout Studio curve.
            aux=empty();addcurve(aux,'body',[[L,k*L,0]],world())
            auxlay=Layout.from_dict(aux)
            mid=auxlay.curves['body'].get_frame(L/2).matrix
            transform=f.matrix@np.linalg.inv(mid)
            edge=[]
            for sign,ss in [(1,np.linspace(0,L,100)),(-1,np.linspace(L,0,100))]:
                for s in ss:
                    ff=auxlay.curves['body'].get_frame(float(s)); p=ff.origin+sign*w/2*ff.x
                    edge.append((transform@np.r_[p,1])[:3])
            q=coord(edge); ax.fill(q[:,0],q[:,1],color=C['body'],alpha=.35,zorder=0)
def finish(fig,ax,name,legend=True):
    ax.set_xlabel('z [m]'); ax.set_ylabel('x [m]');ax.grid(alpha=.16)
    # Explicitly use independent axis scales to expose transverse differences.
    ax.set_aspect('auto');ax.margins(.12)
    if legend:ax.legend(loc='best',framealpha=.94,fontsize=10)
    fig.tight_layout(pad=1.1)
    for ext in ['png','svg']:fig.savefig(ROOT/'plots'/f'{name}.{ext}',dpi=180)
    plt.close(fig)
def standard_plot(name,path,L,extra=None):
    lay,d,meta=DATA[name];fig,ax=plt.subplots(figsize=(9.0,5.15))
    bodies(ax,lay)
    axis_plot(ax,sample(lay,path),meta.get('path_label','Selected path'),C['ts'])
    a=lay.objects['A'].get_frame();axis_plot(ax,[a.origin,a.origin+L*a.tangent],'TT along starting tangent',C['tt'],style='--')
    for n,label in [('A','A'),('TS','TS'),('TT','TT')]:markplot(ax,lay,n,label,shift=((8,12) if n=='TT' else (-18,-22)) if name=='01_mechanical_tangent' else ((8,-20) if n=='TT' else (8,12)),scale=L*.06)
    if extra:extra(ax,lay)
    finish(fig,ax,name)

# 1. A straight mechanical axis, at the tangent to a curved reference path.
d=empty();addcurve(d,'reference',[[8,.8,0]],color=C['ts'])
magnet(d,'M',4,0,position=pos(curve('reference'),[['ts',4]]))
marker(d,'A',pos(obj('M','mechanical_center')),'#263749')
marker(d,'TT',pos(obj('A'),[['tt',3]]),C['tt'])
marker(d,'TS',pos(obj('A'),[['ts',3]],refcurve='reference'),C['ts'])
d['types']['M']['frames']['local_TS'] = placement({'kind':'local_frame','frame':'mechanical_center'},[['ts',3]])
lay=save('01_mechanical_tangent',d,{'L_m':3,'path_label':'Curved global reference','description':'TT from mechanical center follows the straight axis. Global TS follows reference. Local mechanical TS equals TT here.'})
np.testing.assert_allclose(lay.objects['M'].get_frame('local_TS').matrix,lay.objects['TT'].get_frame().matrix,atol=1e-12)
standard_plot('01_mechanical_tangent','reference',3)

# 2. Sector bend: the magnetic centerline is a curved path.
d=empty();magnet(d,'SB',8,.1,position=pos(world(),target='magnetic_entry'))
addcurve(d,'magnetic_path',[[8,.8,0]],obj('SB','magnetic_entry'))
endpoints(d,'magnetic_path',6)
save('02_sbend_magnetic',d,{'L_m':6,'kappa_per_m':.1,'path_label':'S-bend magnetic path','description':'Ideal sector bend with coincident mechanical, magnetic and beam paths.'})
standard_plot('02_sbend_magnetic','magnetic_path',6)

# 3. Rectangular bend, using the current project example's Xsuite geometry.
sys.path.insert(0,str(args.repo/'python_api/examples'))
from xsuite_rbend_axes import rbend_geometry
par=dict(length_straight=4.,angle=.6,rbend_angle_diff=0.,rbend_shift=0.,rbend_compensate_sagitta=True)
g=rbend_geometry(par);L=g['arc_length'];k=g['h']
d=empty();magnet(d,'RB',4,0,width=.9,beam={
    'beam_center':placement(ops=[['tx',g['x_center']],['tt',g['z_center']],['ry',g['center_angle']]]),
    'beam_length':L,'beam_curvature':k,'beam_roll':0})
addcurve(d,'beam_path',[[L,.6,0]],obj('RB','beam_entry'))
addcurve(d,'magnetic_axis',[[4,0,0]],obj('RB','magnetic_entry'),color=C['ref'])
endpoints(d,'beam_path',L/2)
lay=save('03_rbend_beam',d,{'L_m':L/2,'beam_length_m':L,'magnetic_length_m':4,'kappa_per_m':k,'path_label':'Curved beam reference','description':'Straight-body RBend. The field axis is straight, while the chosen beam frame bends.'})
np.testing.assert_allclose(lay.curves['beam_path'].get_frame(L).matrix,lay.objects['RB'].get_frame('beam_exit').matrix,atol=1e-12)
standard_plot('03_rbend_beam','beam_path',L/2,lambda ax,l:axis_plot(ax,sample(l,'magnetic_axis'),'Straight magnetic axis',C['ref'],style='-.'))

# 4. A transparent multi-element example, deliberately schematic.
d=empty();segs=[[5,0,0],[15,.3,0],[10,0,0],[15,.3,0],[15,0,0]]
addcurve(d,'composite_reference',segs)
s=0
for i,(ll,aa,rr) in enumerate(segs):
    if aa:magnet(d,f'E{i}',ll,aa/ll,width=1.0,position=pos(curve('composite_reference'),[['ts',s+ll/2]]))
    s+=ll
endpoints(d,'composite_reference',60,size=.4)
save('04_multiple_elements',d,{'L_m':60,'angle_rad':.6,'path_label':'Composite reference through bends and drifts','description':'Schematic 60 m sector, not LHC data.'})
standard_plot('04_multiple_elements','composite_reference',60)

# 5. Exact S12 reference segments from the repository, in its original frame.
source=json.load(gzip.open(args.repo/'webapp/public/layouts/LHC--LS3.json.gz','rt'))
sector_length=sum(v for op,v in source['objects']['S23']['position']['transformation'] if op=='ts')
remaining=sector_length;segs=[]
for ll,aa,rr in source['reference_curves']['LHC']['segments']:
    use=min(remaining,ll)
    if use>1e-10:segs.append([use,aa*use/ll,rr])
    remaining-=use
    if remaining<1e-9:break
d=empty();addcurve(d,'S12_reference',segs)
d['reference_curves']['S12_reference']['starting_frame']=copy.deepcopy(source['reference_curves']['LHC']['starting_frame'])
endpoints(d,'S12_reference',sector_length,size=15)
lay=save('05_lhc_s12',d,{'L_m':sector_length,'angle_rad':sum(p[1] for p in segs),'segments':len(segs),'path_label':'LHC S12 reference path','description':'Exact LHC reference-curve excerpt. Equipment omitted. S12 is a span container, not one circular bend.'})
standard_plot('05_lhc_s12','S12_reference',sector_length)

def physical_length(segs,s,x,y=0):
    total=0.;rem=s
    for ll,aa,rr in segs:
        u=min(ll,max(0,rem));fac=1+aa/ll*(x*math.cos(rr)+y*math.sin(rr))
        if fac<=0:raise ValueError('Offset path cusp or reversal')
        total+=u*fac;rem-=u
        if rem<=0:break
    if rem>0:total+=rem # Layout Studio's straight continuation beyond domain
    return total
def station_for_length(segs,wanted,x,y=0):
    s=0.;rem=wanted
    for ll,aa,rr in segs:
        fac=1+aa/ll*(x*math.cos(rr)+y*math.sin(rr))
        if rem<=ll*fac:return s+rem/fac
        rem-=ll*fac;s+=ll
    return s+rem
def offset_case(base,name,path,L,x):
    d=copy.deepcopy(DATA[base][1])
    for n in ['A','TS','TT']:d['objects'].pop(n)
    segs=d['reference_curves'][path]['segments']
    sequal=station_for_length(segs,L,x);physical=physical_length(segs,L,x)
    endpoints(d,path,L,x,size=.07 if L<10 else .4)
    marker(d,'EqualPhysicalLength',pos(curve(path),[['ts',sequal],['tx',x]]),C['equal'],.07 if L<10 else .4)
    # An explicit offset curve uses physical arc length as its own TS coordinate.
    offsegs=[[ll*(1+aa/ll*x),aa,rr] for ll,aa,rr in segs]
    addcurve(d,'offset_path',offsegs,curve(path),[['tx',x]],C['offset'])
    marker(d,'OffsetCurveTS',pos(curve('offset_path'),[['ts',L]]),C['equal'],.045 if L<10 else .3)
    lay=save(name,d,{'L_m':L,'x_m':x,'same_station_physical_m':physical,'same_physical_station_m':sequal,
         'metric_increment_m':physical-L,'path_label':DATA[base][2]['path_label'],'description':'Constant local transverse offset. TS on the base curve is station; TS on offset_path is its physical arc length.'})
    np.testing.assert_allclose(lay.objects['OffsetCurveTS'].get_frame().matrix,lay.objects['EqualPhysicalLength'].get_frame().matrix,atol=2e-12)
    # Numerical arc-length check of the offset curve, with exact segment boundaries.
    ss=np.unique(np.r_[np.linspace(0,L,5001),np.cumsum([p[0] for p in segs])]);ss=ss[ss<=L]
    pts=sample(lay,path,ss,x);ell=np.linalg.norm(np.diff(pts,axis=0),axis=1).sum()
    assert abs(ell-physical)<2e-7,(ell,physical)
    REPORT['checks'].append({'case':name,'metric_vs_polyline_error_m':float(ell-physical),'explicit_offset_curve_agrees':True})
    fig,ax=plt.subplots(figsize=(9,5.15));bodies(ax,lay)
    axis_plot(ax,sample(lay,path),'Base reference path',C['ts'])
    axis_plot(ax,sample(lay,path,np.linspace(0,L,500),x),'Constant transverse offset',C['offset'])
    a=lay.objects['A'].get_frame();axis_plot(ax,[a.origin,a.origin+L*a.tangent],'TT from offset start',C['tt'],style='--')
    markplot(ax,lay,'A','A',scale=L*.06)
    markplot(ax,lay,'TS','S: same station',shift=(8,-22),scale=L*.05)
    markplot(ax,lay,'EqualPhysicalLength','P: same physical length',shift=(-158,18),scale=L*.05)
    markplot(ax,lay,'TT','TT',shift=(8,-18),scale=L*.05)
    finish(fig,ax,name)

offset_case('02_sbend_magnetic','06_offset_magnetic','magnetic_path',5,1)
offset_case('03_rbend_beam','07_offset_beam','beam_path',L,.5)
offset_case('04_multiple_elements','08_offset_reference','composite_reference',60,2)

# 9. TS projection discards the old offset; it must be reapplied explicitly.
d=empty();addcurve(d,'path',[[8,.8,0]])
marker(d,'A',pos(curve('path'),[['ts',1],['tx',1]]),'#263749')
marker(d,'ProjectionOnly',pos(obj('A'),[['ts',4]],refcurve='path'),C['tt'])
marker(d,'OffsetRestored',pos(obj('A'),[['ts',4],['tx',1]],refcurve='path'),C['offset'])
marker(d,'ExplicitStation',pos(curve('path'),[['ts',5],['tx',1]]),C['equal'])
lay=save('09_offset_projection_trap',d,{'initial_station_m':1,'delta_station_m':4,'x_m':1,'description':'Object-position TS infers station, then replaces the origin and orientation by the curve frame.'})
np.testing.assert_allclose(lay.objects['OffsetRestored'].get_frame().matrix,lay.objects['ExplicitStation'].get_frame().matrix,atol=1e-12)
fig,ax=plt.subplots(figsize=(9,5.15));axis_plot(ax,sample(lay,'path'),'Base curve',C['ts'])
axis_plot(ax,sample(lay,'path',np.linspace(0,8,500),1),'Offset path x = 1 m',C['offset'])
markplot(ax,lay,'A','A at s = 1 m')
markplot(ax,lay,'ProjectionOnly','TS = 4, offset lost',shift=(8,-20))
markplot(ax,lay,'OffsetRestored','TS = 4 then TX = 1',shift=(8,12))
finish(fig,ax,'09_offset_projection_trap')

# 10. Local TS order versus curve station selection, explicitly in current syntax.
d=empty();magnet(d,'SB',8,.1,position=pos(world(),target='magnetic_entry'))
d['types']['SB']['frames'].update({
 'offset_then_local_TS':placement({'kind':'local_frame','frame':'magnetic_entry'},[['tx',1],['ts',5]]),
 'local_TS_then_offset':placement({'kind':'local_frame','frame':'magnetic_entry'},[['ts',5],['tx',1]])})
addcurve(d,'magnetic_path',[[8,.8,0]],obj('SB','magnetic_entry'))
for n,ref,ops,col in [
 ('LocalFirstOffset',obj('SB','offset_then_local_TS'),[],C['tt']),
 ('TransportedOffset',obj('SB','local_TS_then_offset'),[],C['offset']),
 ('CurveOffsetFirst',curve('magnetic_path'),[['tx',1],['ts',5]],C['equal']),
 ('CurveStationFirst',curve('magnetic_path'),[['ts',5],['tx',1]],C['ts'])]:marker(d,n,pos(ref,ops),col)
lay=save('10_local_order_trap',d,{'description':'Local feature TS is sequential. Curve-reference TS values are gathered first. Local TX then TS is a translated copy of the original-radius curve.'})
np.testing.assert_allclose(lay.objects['CurveOffsetFirst'].get_frame().matrix,lay.objects['CurveStationFirst'].get_frame().matrix,atol=1e-12)
assert np.linalg.norm(lay.objects['LocalFirstOffset'].get_frame().origin-lay.objects['TransportedOffset'].get_frame().origin)>.1
fig,ax=plt.subplots(figsize=(9,5.15));axis_plot(ax,sample(lay,'magnetic_path'),'Magnetic path',C['ts'])
axis_plot(ax,sample(lay,'magnetic_path',np.linspace(0,5,500))+[1,0,0],'Local TX then TS: translated original arc',C['tt'])
axis_plot(ax,sample(lay,'magnetic_path',np.linspace(0,5,500),1),'Local TS then TX: offset arc',C['offset'])
markplot(ax,lay,'LocalFirstOffset','TX then local TS',shift=(-150,15))
markplot(ax,lay,'TransportedOffset','Local TS then TX',shift=(-150,-25))
finish(fig,ax,'10_local_order_trap')

# Exact S12 offset metric, separate from the exaggerated teaching examples.
lay,d,meta=DATA['05_lhc_s12'];segs=d['reference_curves']['S12_reference']['segments'];ss=np.unique(np.r_[np.linspace(0,sector_length,500),np.cumsum([p[0] for p in segs])])
fig,ax=plt.subplots(figsize=(9,4.4));delta=[physical_length(segs,float(s),.1)-s for s in ss]
ax.plot(ss,np.array(delta)*1000,color=C['offset'],lw=2.5)
ax.set_xlabel('S12 reference station s [m]');ax.set_ylabel('Extra offset-path length [mm]');ax.grid(alpha=.2)
fig.tight_layout();fig.savefig(ROOT/'plots/11_s12_offset_metric.png',dpi=180);fig.savefig(ROOT/'plots/11_s12_offset_metric.svg');plt.close(fig)
meta['offset_100mm_extra_length_m']=physical_length(segs,sector_length,.1)-sector_length
REPORT['cases']['05_lhc_s12'].update(meta)
REPORT['checks'].append({'all_json_roundtrips':True,'all_frames_resolved':True,'rbend_endpoint_agrees':True,'straight_local_ts_equals_tt':True,'projection_offset_explicitly_restored':True})
(ROOT/'results.json').write_text(json.dumps(REPORT,indent=2)+'\n')
(ROOT/'cases/list.json').write_text(json.dumps([{'label':n,'path':n+'.json'} for n in DATA],indent=2)+'\n')
print(json.dumps({n:{k:v for k,v in m.items() if k not in ['poses','description','path_label']} for n,m in REPORT['cases'].items()},indent=2))
