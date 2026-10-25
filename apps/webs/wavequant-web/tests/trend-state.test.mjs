import assert from "node:assert/strict";
import test from "node:test";
import {TrendAnalysisSchema,TrendBarsSchema} from "@repo/contracts";
import {trendStateView} from "../public/trend-state.js";
const version="a".repeat(64);
const make=()=>({schemaVersion:1,symbol:"sz.000001",timeframe:"1d",dataVersion:version,complete:true,unavailableRules:[],state:{version:14,currentIndex:2,dynamicDataIndex:[0,0,0,0],enabled:[true,true,false,false],trendState:[null,"多头",null,null],data:{polyline:[{barIndex:0,kind:"L",ordinal:0,knownIndex:1,confirmed:true},{barIndex:1,kind:"H",ordinal:0,knownIndex:1,confirmed:false}],level1:[{sourceIndex:0,knownIndex:1,confirmed:true},{sourceIndex:1,knownIndex:1,confirmed:false}],level2:[],level3:[]}},timing:{dataRequestMs:10,analysisMs:2,totalMs:15}});
const bars=()=>({schemaVersion:1,symbol:"sz.000001",timeframe:"1d",dataVersion:version,source:"akshare",asof:"2020-01-03",priceBasis:"causal_adjusted_equivalent",bars:[{time:"2020-01-02",open:10,high:12,low:9,close:11,volume:1000,factor:1,raw_close:11},{time:"2020-01-03",open:11,high:14,low:10,close:13,volume:1000,factor:1,raw_close:13}]});
const parsers={parseState:v=>TrendAnalysisSchema.parse(v),parseBars:v=>TrendBarsSchema.parse(v)};
test("直接来源引用、确认时钟与动态尾点不因DTO适配改变",()=>{
    const dto=make(),view=trendStateView(dto,bars(),parsers),fixed=view.theory.reversal_trends.strokes[0].points,tail=view.theory.reversal_trends.developing_strokes[0].points;
    assert.equal(fixed.length,1);assert.equal(fixed[0].label,"L");assert.equal(fixed[0].value,9);assert.equal(fixed[0].available_at,"2020-01-03");
    assert.equal(tail.at(-1).value,14);assert.equal(tail.at(-1).state,"developing");assert.equal(dto.state.data.level1[1].sourceIndex,1);
    assert.equal("bars" in dto,false);assert.equal("trendData" in dto.state,false);assert.equal(view.performance.analysis_elapsed_seconds,0.002);
});
test("不同版本行情和越界来源不能拼接",()=>{
    assert.throws(()=>trendStateView(make(),{...bars(),dataVersion:"b".repeat(64)},parsers),/快照不一致/);
    const dto=make();dto.state.data.level1[0].sourceIndex=2;assert.equal(TrendAnalysisSchema.safeParse(dto).success,false);
});
test("辅助引用点不绘制且路径中断保持",()=>{
    const dto=make();dto.state.data.polyline[1].path=2;dto.state.data.polyline.push({barIndex:0,kind:"L",ordinal:0,knownIndex:1,confirmed:true,referenceOnly:true});
    const view=trendStateView(dto,bars(),parsers);
    assert.equal(view.theory.points.length,1);assert.equal(view.theory.lecture_drawing.developing_strokes[0].id,"level0-lecture-2");
});
test("空数据结构可正常读取",()=>{
    const dto=make();dto.state.currentIndex=0;for(const key of Object.keys(dto.state.data))dto.state.data[key]=[];
    const view=trendStateView(dto,{...bars(),bars:[]},parsers);assert.equal(view.bars.length,0);assert.deepEqual(view.theory.points,[]);
});

import {buyEligibilityRanges} from "../public/buy-eligibility-overlay.js";
import {eligibilityBoundaryRanges} from "../public/eligibility-boundary-overlay.js";
test("Rust资格从确认日开始且同日结束只保留边界",()=>{
    const dto=make();dto.state.qualification={enabled:[false,true,false,false],active:[false,true,false,false],intervals:[[],[
        {path:0,startIndex:1,lowIndex:0,endIndex:null,confirmationRule:"confirmed_pullback",endReason:null}
    ],[],[]]};
    let view=trendStateView(dto,bars(),parsers);
    assert.deepEqual(buyEligibilityRanges(view),[{from:1,to:1,start:"2020-01-03",end:"2020-01-03"}]);
    assert.deepEqual(buyEligibilityRanges(view,"2020-01-02"),[]);
    dto.state.qualification.active[1]=false;
    Object.assign(dto.state.qualification.intervals[1][0],{endIndex:1,endReason:"source_bear_trend_confirmed"});
    view=trendStateView(dto,bars(),parsers);
    assert.deepEqual(buyEligibilityRanges(view),[]);
    assert.deepEqual(eligibilityBoundaryRanges(view)[0].kinds,["交易资格确认日","交易资格结束日"]);
    assert.equal(view.orders.length,0);assert.equal(view.signals.length,0);
});
test("资格坐标及关闭级别的数据必须由共享契约拒绝",()=>{
    const dto=make();dto.state.qualification={enabled:[false,false,false,false],active:[false,true,false,false],intervals:[[],[
        {path:0,startIndex:1,lowIndex:0,endIndex:null,confirmationRule:"confirmed_pullback",endReason:null}
    ],[],[]]};
    assert.equal(TrendAnalysisSchema.safeParse(dto).success,false);
    dto.state.qualification.enabled[1]=true;dto.state.qualification.intervals[1][0].lowIndex=2;
    assert.equal(TrendAnalysisSchema.safeParse(dto).success,false);
});
