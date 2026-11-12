//! Raw ordered ActiveModifiers writes. No merging, particle births or lifetimes.
use std::{fs::OpenOptions, io::{self, BufWriter, Write}, path::Path};
use anyhow::{Result, ensure};
use boon_proto::proto::CModifierTableEntry;
use prost::Message;
use serde_json::{Value, json};

pub const FILE: &str = "client_modifier_writes.json";
pub const POLICY: &str = "agr_client_modifier_writes_v1";
const MAX_ROWS: usize = 65_536;
const MAX_PAYLOAD: usize = 16_384;
const MAX_TOTAL: usize = 16 * 1024 * 1024;
const MAX_JSON: usize = 128 * 1024 * 1024;
// Every payload byte needs at most three decimal digits plus a comma. Each
// fixed scalar row fits within 512 bytes; the fixed document header within 1024.
// Thus admitted capture data cannot hit the writer budget and abort actors.
// Actual output I/O remains fatal. Concrete serialization bounds are tested.
const _: () = assert!(4 * MAX_TOTAL + 512 * MAX_ROWS + 1024 < MAX_JSON);

struct Row {
    tick: i32, kind: String, slot: usize, payload: Option<Vec<u8>>, header: Option<Value>,
}

#[derive(Default)]
pub struct Capture {
    first_tick: Option<i32>, last_tick: Option<i32>, table_seen: bool,
    failure: Option<&'static str>, rows: Vec<Row>, bytes: usize,
}

impl Capture {
    fn fail(&mut self, reason: &'static str) { self.failure.get_or_insert(reason); }

    pub fn observe_tick(&mut self, tick: i32, end: i32, table_seen: bool) {
        self.table_seen |= table_seen;
        if !(-1..=end).contains(&tick) { self.fail("tick_out_of_range"); }
        if self.last_tick.is_some_and(|last| tick < last) { self.fail("tick_order"); }
        self.first_tick.get_or_insert(tick);
        self.last_tick = Some(tick);
    }

    pub fn observe_write(&mut self, tick: i32, kind: &str, slot: usize, data: Option<&[u8]>) {
        if self.failure.is_some() { return; }
        if self.last_tick != Some(tick) || !self.table_seen { self.fail("tick_order"); return; }
        if !["Delta", "Snapshot"].contains(&kind) { self.fail("unknown_change_kind"); return; }
        if slot > 65_535 { self.fail("slot_out_of_bounds"); return; }
        if self.rows.len() >= MAX_ROWS { self.fail("row_budget"); return; }
        let size = data.map_or(0, <[u8]>::len);
        if size > MAX_PAYLOAD { self.fail("payload_too_large"); return; }
        if self.bytes + size > MAX_TOTAL { self.fail("payload_budget"); return; }
        let header = data.and_then(|bytes| match CModifierTableEntry::decode(bytes) {
            Ok(v) => Some(json!({"entry_type":v.entry_type,"parent":v.parent,
                "serial_number":v.serial_number,"modifier_subclass":v.modifier_subclass,"int1":v.int1})),
            Err(_) => { self.fail("invalid_payload"); None },
        });
        self.rows.push(Row { tick, kind: kind.to_owned(), slot, payload: data.map(<[u8]>::to_vec), header });
        self.bytes += size;
    }

    fn coverage(&self, end: i32) -> Value {
        let reason = self.failure.or_else(|| if self.last_tick != Some(end) {
            Some("end_tick_not_observed")
        } else if !self.table_seen { Some("table_not_observed") } else { None });
        json!({"availability":if reason.is_none(){"available"}else{"unavailable"},
            "reason":reason,"origin":"signon","table":"ActiveModifiers",
            "table_seen":self.table_seen,"first_observed_tick":self.first_tick,
            "last_observed_tick":self.last_tick,"rows":self.rows.len(),"wire_bytes":self.bytes,
            "scope":"Ordered pre-merge table writes, not active modifier state or particle lifetimes. Absence is not proof of no modifiers."})
    }

    pub fn summary(&self, end: i32) -> Value {
        let c = self.coverage(end);
        json!({"schema_version":1,"policy":POLICY,"file":FILE,
            "availability":c["availability"],"reason":c["reason"],
            "rows":self.rows.len(),"wire_bytes":self.bytes})
    }

    pub fn write_new(&self, path: &Path, start: i32, end: i32) -> Result<()> {
        let header=json!({"schema_version":1,"policy":POLICY,
            "requested_range":{"start_tick":start,"end_tick":end,"inclusive":true},
            "coverage":self.coverage(end)});
        write_rows_new(path,&header,self.rows.iter().map(|row|json!({"tick":row.tick,"kind":row.kind,"slot":row.slot,
            "payload_present":row.payload.is_some(),"wire_bytes":row.payload,"header":row.header})))
    }
}

/// Shared exclusive, bounded streaming publication; no second writer lifecycle.
pub fn write_rows_new(path: &Path, header: &Value, rows: impl IntoIterator<Item=Value>) -> Result<()> {
    let file = OpenOptions::new().write(true).create_new(true).open(path)?;
    let mut out = Limited { inner: BufWriter::new(file), bytes: 0 };
    let mut head = serde_json::to_vec(header)?;
    ensure!(head.pop() == Some(b'}'), "Invalid capture output header");
    out.write_all(&head)?; out.write_all(b",\"rows\":[")?;
    for (index,row) in rows.into_iter().enumerate() {
        if index != 0 { out.write_all(b",")?; }
        serde_json::to_writer(&mut out,&row)?;
    }
    out.write_all(b"]}\n")?;out.flush()?;out.inner.get_ref().sync_all()?;Ok(())
}

struct Limited<W> { inner: W, bytes: usize }
impl<W: Write> Write for Limited<W> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        if data.len() > MAX_JSON - self.bytes { return Err(io::Error::other("Modifier JSON budget exceeded")); }
        let size = self.inner.write(data)?; self.bytes += size; Ok(size)
    }
    fn flush(&mut self) -> io::Result<()> { self.inner.flush() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use crate::projectile_particles::require_fresh;
    fn payload(entry_type: i32, parent: u32, serial: u32, int1: i32) -> Vec<u8> {
        CModifierTableEntry { entry_type:Some(entry_type),parent:Some(parent),
            serial_number:Some(serial),modifier_subclass:Some(1591258939),int1:Some(int1),
            ..Default::default() }.encode_to_vec()
    }
    #[test]
    fn retain_signon_snapshots_removal_reuse_and_omitted_empty_payloads() {
        let mut c=Capture::default();c.observe_tick(1,20,true);
        let first=payload(1,56,188,1);c.observe_write(1,"Delta",127,Some(&first));
        c.observe_tick(10,20,true);c.observe_write(10,"Snapshot",127,Some(&first));
        let removal=payload(2,56,188,1);c.observe_write(10,"Delta",127,Some(&removal));
        let reuse=payload(1,58,189,0);c.observe_write(10,"Delta",127,Some(&reuse));
        c.observe_write(10,"Delta",128,None);c.observe_write(10,"Delta",129,Some(&[]));
        c.observe_tick(20,20,true);
        assert_eq!(c.summary(20)["availability"],"available");assert_eq!(c.rows.len(),6);
        assert_eq!(c.rows[0].payload.as_ref(),Some(&first));
        assert_eq!(c.rows[2].header.as_ref().unwrap()["entry_type"],2);
        assert_eq!(c.rows[3].header.as_ref().unwrap()["serial_number"],189);
        assert!(c.rows[4].payload.is_none());assert_eq!(c.rows[5].payload,Some(vec![]));
        assert_eq!(c.rows[5].header.as_ref().unwrap()["entry_type"],Value::Null);
    }
    #[test]
    fn available_empty_differs_from_missing_table_or_truncated_scan() {
        let mut c=Capture::default();c.observe_tick(20,20,true);
        assert_eq!(c.summary(20)["availability"],"available");assert_eq!(c.summary(20)["rows"],0);
        assert_eq!(c.summary(21)["reason"],"end_tick_not_observed");
        let mut missing=Capture::default();missing.observe_tick(20,20,false);
        assert_eq!(missing.summary(20)["reason"],"table_not_observed");
    }
    #[test]
    fn malformed_and_out_of_order_are_sticky() {
        let mut c=Capture::default();c.observe_tick(1,20,true);c.observe_write(1,"Delta",1,Some(&[0xff]));
        c.observe_tick(20,20,true);c.observe_write(20,"Delta",1,Some(&[]));
        assert_eq!(c.rows.len(),1);assert_eq!(c.summary(20)["reason"],"invalid_payload");
        let mut order=Capture::default();order.observe_tick(2,20,true);order.observe_tick(1,20,true);
        order.observe_tick(20,20,true);assert_eq!(order.summary(20)["reason"],"tick_order");
    }
    #[test]
    fn budgets_refuse_before_copy_and_never_recover() {
        for (kind,slot,bytes,reason) in [("Delta",65536,vec![],"slot_out_of_bounds"),
            ("Other",1,vec![],"unknown_change_kind"),("Delta",1,vec![0;MAX_PAYLOAD+1],"payload_too_large")] {
            let mut c=Capture::default();c.observe_tick(1,1,true);c.observe_write(1,kind,slot,Some(&bytes));
            assert_eq!(c.rows.len(),0);assert_eq!(c.summary(1)["reason"],reason);
        }
        let mut c=Capture::default();c.observe_tick(1,1,true);c.bytes=MAX_TOTAL;
        c.observe_write(1,"Delta",1,Some(&[8,1]));assert_eq!(c.summary(1)["reason"],"payload_budget");
        let mut c=Capture::default();c.observe_tick(1,1,true);
        for _ in 0..MAX_ROWS {c.observe_write(1,"Delta",1,None);}
        c.observe_write(1,"Delta",1,None);assert_eq!(c.rows.len(),MAX_ROWS);assert_eq!(c.summary(1)["reason"],"row_budget");
    }
    #[test]
    fn writer_is_bounded_and_exclusive() {
        let mut w=Limited{inner:Vec::new(),bytes:MAX_JSON-1};assert!(w.write_all(b"ab").is_err());
        assert!(w.inner.is_empty());
        let path=std::env::temp_dir().join(format!("agr-modifier-{}-{}",std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        let c=Capture::default();require_fresh(&path).unwrap();
        fs::write(&path,b"prior").unwrap();assert!(require_fresh(&path).is_err());
        assert!(c.write_new(&path,1,1).is_err());assert_eq!(fs::read(&path).unwrap(),b"prior");
        fs::remove_file(&path).unwrap();
    }
    #[test]
    fn admitted_scalars_and_payloads_fit_the_serialized_budget() {
        let header = json!({"entry_type":i32::MIN,"parent":u32::MAX,
            "serial_number":u32::MAX,"modifier_subclass":u32::MAX,"int1":i32::MIN});
        for wire in [Value::Null, json!([]), json!(vec![255u8;MAX_PAYLOAD])] {
            let payload_bytes = wire.as_array().map_or(0, Vec::len);
            let row = json!({"tick":i32::MAX-1,"kind":"Snapshot","slot":65535,
                "payload_present":!wire.is_null(),"wire_bytes":wire,"header":header});
            assert!(serde_json::to_vec(&row).unwrap().len() <= 512 + 4 * payload_bytes);
        }
        let mut c = Capture::default();
        c.first_tick = Some(i32::MAX-1); c.last_tick = c.first_tick;
        c.table_seen = true; c.bytes = MAX_TOTAL; c.failure = Some("end_tick_not_observed");
        let mut coverage = c.coverage(i32::MAX-1); coverage["rows"] = json!(MAX_ROWS);
        let document = json!({"schema_version":1,"policy":POLICY,
            "requested_range":{"start_tick":i32::MAX-1,"end_tick":i32::MAX-1,"inclusive":true},
            "coverage":coverage,"rows":[]});
        assert!(serde_json::to_vec(&document).unwrap().len() < 1024);
    }
}
