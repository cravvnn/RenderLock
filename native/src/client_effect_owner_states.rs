//! Exact selected-owner network words; absence is not a zero or a visibility decision.
use std::{collections::BTreeSet,path::Path};
use anyhow::Result;
use serde_json::{Value,json};
use pbdems2::entity::field_path::FieldPath;

pub const FILE:&str="client_effect_owner_states.json";
pub const POLICY:&str="agr_client_effect_owner_states_v1";
const MAX_ROWS:usize=200_000;
const MAX_WORDS:usize=8_000_000;
const MAX_ARRAY:usize=128;
const MAX_JSON:usize=128*1024*1024;

fn array_keys(serializer:&boon::Serializer,path:&str)->Option<Vec<u64>> {
    let mut fp=FieldPath::unpack(serializer.resolve_field_key(path)?);
    let indices=fp.data.get(..=fp.last)?;
    let mut field=serializer.fields.get(*indices.first()? as usize)?.as_ref();
    for index in &indices[1..] {field=field.get_child(*index as usize)?;}
    let ty=&field.field_type;let length=ty.array_length?;
    if ty.base_type!="uint32"||ty.pointer||ty.generic_type.is_some()||ty.count.is_some()
        ||length==0||length>MAX_ARRAY {return None;}
    let next=fp.last.checked_add(1)?;fp.data.get(next)?;
    (0..length).map(|index|{
        field.get_child(index)?;fp.data[next]=u8::try_from(index).ok()?;fp.last=next;Some(fp.pack())
    }).collect()
}

fn words(entity:&boon::Entity,serializer:&boon::Serializer,name:&str)->Option<Vec<u32>> {
    array_keys(serializer,name)?.into_iter().map(|key|u32::try_from(entity.get_u64(Some(key))?).ok()).collect()
}

struct Row {
    tick:i32,index:i32,serial:u32,hero:i64,active:bool,life:Option<u8>,
    enabled:Option<Vec<u32>>,predicted:Option<Vec<u32>>,disabled:Option<Vec<u32>>,
}
impl Row {
    fn arrays(&self)->[&Option<Vec<u32>>;3]{[&self.enabled,&self.predicted,&self.disabled]}
    fn word_count(&self)->usize{self.arrays().into_iter().filter_map(|a|a.as_ref()).map(Vec::len).sum()}
    fn incomplete(&self)->bool{self.life.is_none()||self.arrays().into_iter().any(Option::is_none)}
    fn json(&self)->Value {
        json!({"tick":self.tick,"entity_id":format!("{}:{}",self.index,self.serial),
            "entity_index":self.index,"entity_serial":self.serial,"hero_id":self.hero,"active":self.active,
            "life_state":self.life,"enabled_state_words":self.enabled,
            "predicted_state_words":self.predicted,"disabled_state_words":self.disabled})
    }
}

#[derive(Default)]
pub struct Capture {
    first:Option<i32>,last:Option<i32>,failure:Option<&'static str>,
    rows:Vec<Row>,words:usize,json_bytes:usize,incomplete:usize,seen:BTreeSet<(i32,u32)>,
}
impl Capture {
    fn fail(&mut self,reason:&'static str){self.failure.get_or_insert(reason);}
    pub fn observe_tick(&mut self,tick:i32,end:i32) {
        if !(-1..=end).contains(&tick){self.fail("tick_out_of_range");}
        if self.last.is_some_and(|last|tick<last){self.fail("tick_order");}
        if self.last!=Some(tick){self.seen.clear();}
        self.first.get_or_insert(tick);self.last=Some(tick);
    }
    pub fn observe(&mut self,entity:&boon::Entity,serializer:&boon::Serializer,tick:i32,start:i32,end:i32,hero:i64) {
        if tick<start||tick>end||self.failure.is_some(){return;}
        let life=entity.get_u64(serializer.resolve_field_key("m_lifeState")).and_then(|v|u8::try_from(v).ok());
        self.push(Row{tick,index:entity.index,serial:entity.serial,hero,active:entity.active,life,
            enabled:words(entity,serializer,"m_pModifierProp.m_bvEnabledStateMask"),
            predicted:words(entity,serializer,"m_pModifierProp.m_bvEnabledPredictedStateMask"),
            disabled:words(entity,serializer,"m_pModifierProp.m_bvDisabledStateMask")});
    }
    fn push(&mut self,row:Row) {
        if self.failure.is_some(){return;}
        if self.last!=Some(row.tick){self.fail("tick_order");return;}
        if !(0..0x4000).contains(&row.index)||row.serial>=0x40000||row.hero<=0 {self.fail("owner_identity");return;}
        if self.seen.contains(&(row.index,row.serial)){self.fail("duplicate_owner_tick");return;}
        if row.arrays().into_iter().filter_map(|a|a.as_ref()).any(|a|a.is_empty()||a.len()>MAX_ARRAY){self.fail("array_bounds");return;}
        if self.rows.len()>=MAX_ROWS{self.fail("row_budget");return;}
        let count=row.word_count();if self.words+count>MAX_WORDS{self.fail("word_budget");return;}
        let size=match serde_json::to_vec(&row.json()){Ok(v)=>v.len()+1,Err(_)=>{self.fail("json_budget");return;}};
        if self.json_bytes+size>MAX_JSON-4096{self.fail("json_budget");return;}
        self.json_bytes+=size;self.words+=count;self.incomplete+=usize::from(row.incomplete());
        self.seen.insert((row.index,row.serial));self.rows.push(row);
    }
    fn coverage(&self,end:i32)->Value {
        let reason=self.failure.or_else(||(self.last!=Some(end)).then_some("end_tick_not_observed"));
        json!({"availability":if reason.is_none(){"available"}else{"unavailable"},"reason":reason,
            "origin":"signon","first_observed_tick":self.first,"last_observed_tick":self.last,
            "rows":self.rows.len(),"words":self.words,"incomplete_rows":self.incomplete,
            "scope":"Selected-owner output-range snapshots from signon reconstruction. Missing fields remain null. Raw availability is not client visibility, modifier lifetime or effect coverage."})
    }
    pub fn summary(&self,end:i32)->Value {
        let c=self.coverage(end);json!({"schema_version":1,"policy":POLICY,"file":FILE,
            "availability":c["availability"],"reason":c["reason"],"rows":self.rows.len(),
            "words":self.words,"incomplete_rows":self.incomplete})
    }
    pub fn write_new(&self,path:&Path,start:i32,end:i32)->Result<()> {
        crate::client_modifier_writes::write_rows_new(path,&json!({"schema_version":1,"policy":POLICY,
            "requested_range":{"start_tick":start,"end_tick":end,"inclusive":true},
            "coverage":self.coverage(end)}),self.rows.iter().map(Row::json))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn row(tick:i32,index:i32)->Row{Row{tick,index,serial:283,hero:63,active:true,life:Some(0),
        enabled:Some(vec![0;12]),predicted:Some(vec![0,128,0,8,0,0,0,0,0,0,0,1]),disabled:Some(vec![0;12])}}
    #[test]
    fn words_and_missing_fields_are_retained_without_name_collisions_or_defaults(){
        let mut c=Capture::default();c.observe_tick(10,20);c.push(row(10,58));
        let mut missing=row(10,56);missing.life=None;missing.predicted=None;c.push(missing);
        c.observe_tick(20,20);
        assert_eq!(c.summary(20)["availability"],"available");assert_eq!(c.incomplete,1);
        assert_eq!(c.rows[0].json()["predicted_state_words"][11],1);assert_eq!(c.words,60);
        assert!(c.rows[1].json()["predicted_state_words"].is_null());assert!(c.rows[1].json()["life_state"].is_null());
    }
    #[test]
    fn duplicate_identity_and_order_failure_are_sticky(){
        let mut c=Capture::default();c.observe_tick(10,20);c.push(row(10,58));c.push(row(10,58));
        c.observe_tick(20,20);c.push(row(20,58));assert_eq!(c.rows.len(),1);
        assert_eq!(c.summary(20)["reason"],"duplicate_owner_tick");
        let mut c=Capture::default();c.observe_tick(10,20);c.push(row(10,0x4000));
        assert_eq!(c.summary(20)["reason"],"owner_identity");
        let mut c=Capture::default();c.observe_tick(10,20);c.observe_tick(9,20);c.observe_tick(20,20);
        assert_eq!(c.summary(20)["reason"],"tick_order");
    }
    #[test]
    fn empty_raw_stream_and_unreached_end_are_distinct(){
        let mut c=Capture::default();c.observe_tick(20,20);assert_eq!(c.summary(20)["availability"],"available");
        assert_eq!(c.summary(20)["rows"],0);assert_eq!(c.summary(21)["reason"],"end_tick_not_observed");
    }
    #[test]
    fn budget_refusal_precedes_retention(){
        let mut c=Capture::default();c.observe_tick(10,20);c.words=MAX_WORDS;c.push(row(10,58));
        assert!(c.rows.is_empty());assert_eq!(c.summary(20)["reason"],"word_budget");
        let mut c=Capture::default();c.observe_tick(10,20);c.json_bytes=MAX_JSON-4096;c.push(row(10,58));
        assert!(c.rows.is_empty());assert_eq!(c.summary(20)["reason"],"json_budget");
        let mut c=Capture::default();c.observe_tick(10,20);let mut r=row(10,58);r.enabled=Some(vec![0;129]);c.push(r);
        assert!(c.rows.is_empty());assert_eq!(c.summary(20)["reason"],"array_bounds");
    }
}
