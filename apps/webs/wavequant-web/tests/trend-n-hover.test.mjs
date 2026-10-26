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

// 海洋王已有v31结果的N45：只被二级订阅，但ABC和攻击位于12/02至01/06的一级上涨段。
function sharedStageFixture() {
    const rows = [
        ["2018-10-19", 6, 5], ["2019-12-02", 7.791401254678171, 7.382631395529714],
        ["2019-12-03", 7.77901428924943, 7.506501049817125], ["2019-12-04", 7.7542403583919475, 7.568435876960831],
        ["2019-12-05", 8.026753597824253, 7.605596773247053], ["2019-12-06", 8.125849321254181, 7.927657874394324],
        ["2019-12-09", 8.274492906399075, 8.051527528681735], ["2019-12-13", 8.683262765547532, 8.435523456972708],
        ["2020-01-06", 9.711380896133045, 8.980549935837319], ["2020-01-20", 9.389319794985777, 8.980549935837319],
        ["2020-02-04", 7.865723047250617, 7.258761741242303],
    ];
    const record = { direction: "up", points: [{barIndex:1,ordinal:0},{barIndex:2,ordinal:1},{barIndex:3,ordinal:1}],
        knownIndex:4, attackIndex:4, squeezeIndex:6, defense:7.605596773247053, observedThrough:10, invalidatedIndex:10,
        targets:[{knownIndex:6,prices:[8.670875800118791,9.31499800241333,null,null],reachedAt:[null,null]},
            {knownIndex:9,prices:[8.670875800118791,9.31499800241333,10.863368681005973,null],reachedAt:[7,8]}] };
    return {symbol:"sz.002724",bars:rows.map(([time,high,low])=>({time,high,low})),
        trendState:{n:{records:[record],spans:[[],[],[{nIndex:0,path:0,originIndex:0,startIndex:4,endIndex:10}],[]]}}};
}

test("一级段展示仅由二级订阅的同段共享N，保留来源及已发布目标", () => {
    const data=sharedStageFixture(), frozen=structuredClone(data), reader=createTrendNReader(data);
    const stage={level:1,path:0,direction:"up",from:"2019-12-02",to:"2020-01-06"};
    assert.equal(reader.read(1,0,"2019-12-05","2020-02-04"),null);
    const n=reader.inspectStage(stage,"2019-12-05","2020-02-04");
    assert.equal(n.level,2);assert.equal(n.displayStage.level,1);assert.equal(n.nIndex,0);
    assert.equal(n.squeeze,"2019-12-09");assert.equal(n.targets[0].price,8.670875800118791);
    assert.equal(n.targets[1].price,9.31499800241333);assert.equal(n.targets[2].price,10.863368681005973);
    assert.ok(trendNTargetGuides(n).every(guide=>guide.display_at==="2019-12-05"));
    const causal=reader.read(2,0,"2019-12-05","2020-02-04");
    assert.equal(causal.squeeze,null);assert.ok(causal.targets.every(target=>target.price===null));
    assert.ok(reader.inspectStage(stage,"2019-12-05","2019-12-05").targets.every(target=>target.price===null));
    assert.deepEqual(data,frozen);
});

test("共享N展示不跨方向、路径或一级段，失效后不沿用，二三级入口不借其他级别", () => {
    const data=sharedStageFixture(), reader=createTrendNReader(data);
    const stage={level:1,path:0,direction:"up",from:"2019-12-02",to:"2020-01-06"};
    for(const changed of [{direction:"down"},{path:1},{from:"2019-12-03"},{to:"2019-12-04"}])
        assert.equal(reader.inspectStage({...stage,...changed},"2019-12-05","2020-02-04"),null);
    assert.equal(reader.inspectStage({...stage,to:"2020-02-04"},"2020-02-04","2020-02-04"),null);
    data.trendState.n.spans[3]=data.trendState.n.spans[2];data.trendState.n.spans[2]=[];
    assert.equal(createTrendNReader(data).inspectStage({...stage,level:2},"2019-12-05","2020-02-04"),null);
});

test("倒N共享参考及N字母命中保留一级展示归属，不改变实际来源级别", () => {
    const data=sharedStageFixture();
    data.bars=data.bars.map(bar=>({...bar,high:20-bar.low,low:20-bar.high}));
    const record=data.trendState.n.records[0];record.direction="down";record.defense=20-record.defense;
    for(const snapshot of record.targets) snapshot.prices=snapshot.prices.map(price=>price===null?null:20-price);
    const stage={level:1,path:0,direction:"down",from:"2019-12-02",to:"2020-01-06"};
    const n=createTrendNReader(data).inspectStage(stage,"2019-12-05","2020-02-04");
    assert.equal(n.up,false);assert.equal(n.level,2);assert.deepEqual(n.displayStage,stage);
    const overlay=new TrendNHoverOverlay();overlay.setModel(n);assert.deepEqual(overlay.model.displayStage,stage);
    overlay.setModel({...n,displayStage:undefined});assert.equal(overlay.model.displayStage,undefined);
});
