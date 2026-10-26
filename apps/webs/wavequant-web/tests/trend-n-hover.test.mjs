import assert from "node:assert/strict";
import test from "node:test";
import { createTrendNReader, trendSegmentAt, trendStageAt, trendNColor, trendNTargetGuides } from "../public/trend-n-hover.js";
function fixture() {
    const bars = Array.from({ length: 9 }, (_, index) => ({ time: `2020-01-${String(index + 1).padStart(2, "0")}`, high: 15 + index, low: 10 + index }));
    const record = { direction: "up", points: [{barIndex:0,ordinal:0},{barIndex:1,ordinal:0},{barIndex:2,ordinal:0}],
        knownIndex:2,attackIndex:3,squeezeIndex:4,defense:12,observedThrough:8,invalidatedIndex:null,
        targets:[{knownIndex:4,prices:[20,25,null,null],reachedAt:[null,null]}, {knownIndex:7,prices:[20,25,30,40],reachedAt:[6,null]}] };
    const span = {nIndex:0,path:0,originIndex:0,startIndex:2,endIndex:null};
    return {bars,trendState:{n:{records:[record],spans:[[],[span],[{...span}],[]]}}};
}
test("历史悬停只读取当日快照，远端目标和未来达标日不回填", () => {
    const data = fixture(), reader = createTrendNReader(data);
    assert.equal(reader.read(2,0,"2020-01-02","2020-01-09"),null);
    const waiting = reader.read(2,0,"2020-01-04","2020-01-09");
    assert.equal(waiting.squeeze,null);assert.ok(waiting.targets.every(target=>target.price===null));
    const early = reader.read(2,0,"2020-01-06","2020-01-09");
    assert.deepEqual(early.targets.map(target=>target.price),[20,25,null,null]);assert.equal(early.targets[0].reached,null);
    assert.deepEqual(reader.read(2,0,"2020-01-08","2020-01-09").targets.map(target=>target.price),[20,25,30,40]);
    assert.equal(reader.read(2,0,"2020-01-08","2020-01-06"),null);
});
test("N标识和趋势段回看同一N已发布目标，光标早于轧空日也能查看", () => {
    const reader=createTrendNReader(fixture());
    const historical=reader.read(2,0,"2020-01-04","2020-01-09","up");
    assert.ok(historical.targets.every(target=>target.price===null));
    const viewed=reader.inspect(2,0,"2020-01-04","2020-01-09","up");
    assert.equal(viewed.nIndex,historical.nIndex);assert.equal(viewed.time,"2020-01-04");
    assert.equal(viewed.inspected,true);assert.equal(viewed.snapshotTime,"2020-01-09");
    assert.deepEqual(viewed.targets.map(target=>target.price),[20,25,30,40]);
    assert.equal(viewed.squeeze,"2020-01-05");assert.equal(viewed.targets[0].reached,"2020-01-07");
});
test("N回看受回放截止、本级使用结束及破位限制，不切换后续N", () => {
    const data=fixture(), reader=createTrendNReader(data);
    const replay=reader.inspect(2,0,"2020-01-04","2020-01-04","up");
    assert.ok(replay.targets.every(target=>target.price===null));assert.equal(replay.squeeze,null);
    assert.equal(reader.inspect(2,0,"2020-01-04","2020-01-09","down"),null);
    data.trendState.n.spans[1][0].endIndex=6;
    data.trendState.n.records[0].invalidatedIndex=7;
    const bounded=createTrendNReader(data);
    assert.equal(bounded.inspect(1,0,"2020-01-04","2020-01-09").snapshotTime,"2020-01-06");
    const beforeBreak=bounded.inspect(2,0,"2020-01-04","2020-01-09");
    assert.equal(beforeBreak.snapshotTime,"2020-01-07");
    assert.deepEqual(beforeBreak.targets.map(target=>target.price),[20,25,null,null]);
    assert.equal(bounded.inspect(2,0,"2020-01-08","2020-01-09"),null);
});
test("一级趋势整个日期范围回看本段N，形成前不要求贴近N或趋势线", () => {
    const reader=createTrendNReader(fixture());
    const stage={level:1,path:0,direction:"up",from:"2020-01-01",to:"2020-01-09"};
    for(const time of ["2020-01-01","2020-01-04","2020-01-08"]){
        const n=reader.inspectStage(stage,time,"2020-01-09");
        assert.equal(n.nIndex,0);assert.equal(n.time,time);assert.equal(n.targets[0].price,20);
    }
    assert.equal(reader.inspectStage(stage,"2020-01-01","2020-01-02"),null);
    assert.equal(reader.inspectStage({...stage,path:1},"2020-01-04","2020-01-09"),null);
    assert.equal(reader.inspectStage({...stage,direction:"down"},"2020-01-04","2020-01-09"),null);
    assert.equal(reader.inspectStage({...stage,from:"2020-01-02"},"2020-01-04","2020-01-09"),null);
});
test("跨级读同一N，失效日与本级趋势结束日均不沿用旧N", () => {
    const data = fixture();
    data.trendState.n.spans[1][0].endIndex=6;data.trendState.n.records[0].invalidatedIndex=8;
    const reader=createTrendNReader(data);
    assert.equal(reader.read(1,0,"2020-01-06","2020-01-09").nIndex,reader.read(2,0,"2020-01-06","2020-01-09").nIndex);
    assert.equal(reader.read(1,0,"2020-01-07","2020-01-09"),null);
    assert.ok(reader.read(2,0,"2020-01-07","2020-01-09"));
    assert.equal(reader.read(2,0,"2020-01-09","2020-01-09"),null);
});
test("倒N读取高低高，点顺序和数据快照保持只读", () => {
    const data=fixture();data.trendState.n.records[0].direction="down";
    const frozen=structuredClone(data), result=createTrendNReader(data).read(2,0,"2020-01-06","2020-01-09");
    assert.deepEqual(result.shape.slice(0,3).map(point=>point.value),[15,11,17]);assert.deepEqual(data,frozen);
});
test("只命中当前显示的级别和路径，显示衔接及远离线段不匹配", () => {
    const segment=[{x:0,y:0,point:{path:2}},{x:100,y:100,point:{path:2,trend_state:"空翻多"}}];
    const groups=[{stroke:{trend_level:2,kind:"secondary"},segments:[segment]}];
    assert.equal(trendSegmentAt(groups,50,50).level,2);assert.equal(trendSegmentAt(groups,50,50).path,2);
    assert.equal(trendSegmentAt(groups,50,80),null);
    groups[0].stroke.kind="secondary-connection";assert.equal(trendSegmentAt(groups,50,50),null);
});

test("同趋势旧N失效后只读取替代索引，不串用其他级别和路径", () => {
    const data=fixture(), n=data.trendState.n;
    n.spans[2][0].endIndex=6;
    n.records.push({...structuredClone(n.records[0]),knownIndex:6,attackIndex:7,squeezeIndex:8,
        targets:[{knownIndex:8,prices:[50,60,null,null],reachedAt:[null,null]}]});
    n.spans[2].push({nIndex:1,path:0,originIndex:0,startIndex:6,endIndex:null});
    const reader=createTrendNReader(data);
    assert.equal(reader.read(2,0,"2020-01-06","2020-01-09").nIndex,0);
    assert.equal(reader.read(2,0,"2020-01-07","2020-01-09").nIndex,1);
    assert.equal(reader.read(1,0,"2020-01-07","2020-01-09").nIndex,0);
    assert.equal(reader.read(2,1,"2020-01-09","2020-01-09"),null);
});

test("波段内普通K线悬停和日期定位不要求贴近趋势线，同样读取当日N", () => {
    const data=fixture(), reader=createTrendNReader(data);
    assert.equal(reader.contextAt(2,"2020-01-06","2020-01-09").n.nIndex,0);
    assert.deepEqual(reader.contextAt(2,"2020-01-06","2020-01-09").n,reader.read(2,0,"2020-01-06","2020-01-09"));
    assert.equal(reader.contextAt(3,"2020-01-06","2020-01-09").n,null);
    assert.equal(reader.contextAt(2,"2020-01-08","2020-01-06"),null);
});
test("目标复用既有标签图层，仅传已确认价位且保留图外提示", () => {
    const model=createTrendNReader(fixture()).read(2,0,"2020-01-06","2020-01-09");
    const guides=trendNTargetGuides(model);
    assert.deepEqual(guides.map(guide=>guide.stage),["one_p","two_t","n_defense"]);
    assert.ok(guides.every(guide=>guide.display_at==="2020-01-04"&&guide.hideWhenPriceOffscreen!==true));
});

test("上涨段不借用同级倒N，下跌段不借用同级正N", () => {
    const data=fixture(), reader=createTrendNReader(data);
    assert.equal(reader.read(2,0,"2020-01-06","2020-01-09","down"),null);
    assert.ok(reader.read(2,0,"2020-01-06","2020-01-09","up"));
    assert.equal(reader.contextAt(2,"2020-01-06","2020-01-09","down").n,null);
    data.trendState.n.records[0].direction="down";
    assert.equal(createTrendNReader(data).read(2,0,"2020-01-06","2020-01-09","up"),null);
});
test("线段方向来自已绘制端点，不由日期命中的N方向决定", () => {
    const a={x:0,y:100,point:{time:"2020-01-01",value:10,path:0}},b={x:100,y:0,point:{time:"2020-01-09",value:15,path:0}};
    const groups=[{stroke:{trend_level:2,kind:"secondary"},segments:[[a,b]]}];
    assert.equal(trendStageAt(groups,"2020-01-06",2).direction,"up");
    assert.equal(trendSegmentAt(groups,50,50).direction,"up");
    assert.equal(trendStageAt(groups,"2020-01-10",2),null);
});
test("上涨与下跌目标使用不同方向色，下跌防守统一称杀多高", () => {
    const up=createTrendNReader(fixture()).read(2,0,"2020-01-06","2020-01-09");
    const down={...up,up:false};
    assert.notEqual(trendNColor(true),trendNColor(false));
    const upGuides=trendNTargetGuides(up),downGuides=trendNTargetGuides(down);
    assert.ok(upGuides.every(row=>row.color===trendNColor(true)));
    assert.ok(downGuides.every(row=>row.color===trendNColor(false)));
    assert.equal(upGuides[0].name,"一饱");assert.equal(downGuides[0].name,"一饱");
    assert.deepEqual(up.targets.map(row=>row.name),["一饱","二吐","五顶","十满"]);
    assert.equal(downGuides.at(-1).name,"杀多高");
});

import { TrendNHoverOverlay } from "../public/trend-n-hover-overlay.js";
test("N字母、端点及N线段命中同一来源，不随A/B/C所在K线日期切换", () => {
    const overlay=new TrendNHoverOverlay();overlay.model={level:1,nIndex:7};
    overlay.projected=[{x:20,y:70,label:"N·A"},{x:50,y:20,label:"N·B"},{x:80,y:60,label:"N·C"}];
    for(const [x,y]of[[20,70],[60,9],[35,45]])assert.equal(overlay.hitTest(x,y).externalId,"trend-n:1:7");
    assert.equal(overlay.hitTest(200,200),null);
});
test("正N和倒N在一级整段内回看，目标固定攻击日而不随鼠标日期移动", () => {
    for(const direction of ["up","down"]){
        const data=fixture();data.trendState.n.records[0].direction=direction;
        const reader=createTrendNReader(data),stage={level:1,path:0,direction,from:"2020-01-01",to:"2020-01-09"};
        const early=reader.inspectStage(stage,"2020-01-01","2020-01-09");
        const late=reader.inspectStage(stage,"2020-01-08","2020-01-09");
        assert.equal(early.nIndex,late.nIndex);
        assert.deepEqual(trendNTargetGuides(early),trendNTargetGuides(late));
        assert.ok(trendNTargetGuides(late).every(guide=>guide.display_at==="2020-01-04"));
        const waiting=reader.inspectStage(stage,"2020-01-01","2020-01-03");
        assert.equal(waiting.attack,null);
        assert.ok(trendNTargetGuides({...waiting,targets:early.targets}).every(guide=>guide.display_at==="2020-01-03"));
    }
});
