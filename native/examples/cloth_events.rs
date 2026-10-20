//! Offline typed cloth animation-event inventory. No activation semantics inferred.
use std::{collections::BTreeMap, fs::{self,OpenOptions}, io::{BufWriter,Write}, path::Path};
use anyhow::{Context as _,ensure};
use boon_proto::proto::{CDemoFullPacket,CDemoPacket,CnetMsgTick,CMsgClothStiffenAnimEvent,CMsgClothEffectAnimEvent};
use prost::Message;
use serde_json::{json,Value};

fn main()->anyhow::Result<()> {
    let args:Vec<String>=std::env::args().collect();
    ensure!(args.len()==4,"Usage: cloth_events DEM NEW_OUTPUT_JSON END_TICK");
    let end:i32=args[3].parse().context("END_TICK must be an integer")?;
    ensure!(end>=0,"Negative end tick");
    ensure!(!Path::new(&args[2]).exists(),"Preserve previous evidence; choose a new output");
    let data=fs::read(&args[1])?;
    let demo=pbdems2::demo::Demo::new(&data)?;
    let mut body=Vec::new();let mut payload=Vec::new();let mut rows=Vec::<Value>::new();
    let mut counts=BTreeMap::<u32,usize>::new();let mut packet_order=0usize;
    let mut server_tick=None;let mut latest_tick=0;
    for frame in demo.commands() {
        let frame=frame?;let header=frame.header();
        if header.tick>end {continue;}
        latest_tick=latest_tick.max(header.tick);
        if ![7,8,13].contains(&header.cmd) {continue;}
        frame.decode_body(&mut body)?;
        let packet=if header.cmd==13 {CDemoFullPacket::decode(body.as_slice())?.packet}
                   else {Some(CDemoPacket::decode(body.as_slice())?)};
        let Some(packet)=packet else {continue;};
        for message in pbdems2::packet::PacketMessageIter::new(&packet.data.unwrap_or_default()) {
            let message=message?;packet_order+=1;let kind=message.message_type();
            *counts.entry(kind).or_default()+=1;
            if ![4,213,214].contains(&kind) {continue;}
            message.copy_payload(&mut payload)?;
            if kind==4 {server_tick=CnetMsgTick::decode(payload.as_slice())?.tick;continue;}
            ensure!(payload.len()<=16384,"Cloth event payload exceeds inventory bound");
            let value=if kind==213 {
                let value=CMsgClothStiffenAnimEvent::decode(payload.as_slice())?;
                ensure!([value.intensity,value.length,value.speed_in,value.speed_out].iter()
                    .all(|value|value.is_none_or(f32::is_finite)),"Nonfinite cloth stiffening event");
                serde_json::to_value(value)?
            } else {serde_json::to_value(CMsgClothEffectAnimEvent::decode(payload.as_slice())?)?};
            rows.push(json!({"demo_tick":header.tick,"server_tick":server_tick,"packet_order":packet_order,
                "message_id":kind,"data":value,"wire_bytes":payload}));
        }
    }
    let report=json!({"status":"EXTRACTED","demo":args[1],"inclusive_end_tick":end,
        "latest_observed_demo_tick":latest_tick,"packet_message_counts":counts,"events":rows,
        "scope":"Typed recorded cloth messages from signon. Source entity indices are not yet bound to serials. No default activation, operation enum, event-consumer or simulation parity asserted."});
    let mut writer=BufWriter::new(OpenOptions::new().write(true).create_new(true).open(&args[2])?);
    serde_json::to_writer_pretty(&mut writer,&report)?;writer.flush()?;writer.get_ref().sync_all()?;
    println!("Extracted {} recorded cloth events through tick {}",rows.len(),end);
    Ok(())
}
