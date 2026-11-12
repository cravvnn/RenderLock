//! Network projectile history, not particle-manager events or client visibility.
use std::{collections::{BTreeMap, BTreeSet}, fs::{self, OpenOptions}, io::{self, BufWriter, Write}, path::Path};
use anyhow::{Context as _, Result, ensure};
use serde_json::{Value, json};

pub const CLASS: &str = "CCitadel_Projectile_BatSwarmProjectile";
pub const FILE: &str = "network_projectiles.json";
pub const POLICY: &str = "agr_network_projectile_fields_v2";
const MAX_IDENTITIES: usize = 50_000;
const MAX_TRACKS: usize = 4_096;
const MAX_TRACK_ROWS: usize = 4_096;
const MAX_ROWS: usize = 500_000;
const MAX_JSON: usize = 256 * 1024 * 1024;

pub fn require_fresh(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path) {
        Ok(_) => anyhow::bail!("Refusing to overwrite {}", path.display()),
        Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e).with_context(|| format!("Checking reserved output {}", path.display())),
    }
}

#[derive(Default)]
pub struct Capture {
    first: BTreeMap<String, i32>, tracks: BTreeMap<String, Value>, present: BTreeSet<String>,
    first_tick: Option<i32>, last_tick: Option<i32>, open_tick: Option<i32>, samples: usize,
    failure: Option<&'static str>,
}

impl Capture {
    // This optional sidecar must not abort unrelated actor/clock extraction.
    // A failed capture is terminal: discard every partial track, do not retry,
    // and publish unavailable coverage rather than an apparently empty success.
    fn invalidate(&mut self, reason: &'static str) {
        self.failure.get_or_insert(reason);
        self.first.clear(); self.tracks.clear(); self.present.clear();
        self.samples = 0; self.open_tick = None;
    }

    pub fn capture_tick<F>(&mut self, tick: i32, end: i32, observe: F)
    where F: FnOnce(&mut Self) -> Result<()> {
        if self.failure.is_some() { return; }
        let result = (|| {
            self.begin_tick(tick, end)?; observe(self)?; self.finish_tick(tick)
        })();
        if let Err(error) = result {
            eprintln!("AGR: projectile capture unavailable: {error:#}");
            self.invalidate("capture_failed");
        }
    }

    pub fn begin_tick(&mut self, tick: i32, end: i32) -> Result<()> {
        ensure!(self.open_tick.is_none() && (-1..=end).contains(&tick)
            && self.last_tick.is_none_or(|last| last < tick), "Projectile scan tick order/range invalid");
        self.first_tick.get_or_insert(tick); self.last_tick = Some(tick); self.open_tick = Some(tick);
        Ok(())
    }

    pub fn observe(&mut self, entity: &boon::Entity, serializer: &boon::Serializer,
                   tick: i32, selected_owner: bool) -> Result<()> {
        ensure!(entity.index >= 0 && entity.index < 0x4000 && entity.serial < 0x40000,
            "Projectile identity outside recorded handle format");
        let id = format!("{}:{}", entity.index, entity.serial);
        let mut fields = BTreeMap::<String, Value>::new();
        if selected_owner || self.tracks.contains_key(&id) {
            for (key, value) in &entity.fields {
                let Some(name) = serializer.field_name_for_key(*key) else { continue; };
                if name.starts_with("CBodyComponent.m_skeletonInstance.m_vecOrigin.") || [
                    "CBodyComponent.m_skeletonInstance.m_angRotation", "CBodyComponent.m_skeletonInstance.m_flScale",
                    "CBodyComponent.m_skeletonInstance.m_hParent", "m_hOwnerEntity", "m_hParticleDef", "m_vInitialPosition",
                    "m_vInitialVelocity", "m_vecSpawnPosition", "m_flCreateTime", "m_flMaxLifetime", "m_flProjectileSpeed",
                    "m_bNoInterpolate", "m_bGravityDisabled", "m_flGravityScale", "m_flTimeScale", "m_MoveType", "m_fEffects"
                ].contains(&name.as_str()) { fields.insert(name, serde_json::to_value(value)?); }
            }
        }
        self.observe_fields(&id, tick, entity.active, selected_owner, fields)
    }

    fn observe_fields(&mut self, id: &str, tick: i32, active: bool, selected_owner: bool,
                      fields: BTreeMap<String, Value>) -> Result<()> {
        ensure!(self.open_tick == Some(tick) && tick >= 0, "Projectile outside open nonnegative observation");
        ensure!(self.first.len() < MAX_IDENTITIES || self.first.contains_key(id), "Projectile identity budget exceeded");
        let first = *self.first.entry(id.to_owned()).or_insert(tick);
        ensure!(self.present.insert(id.to_owned()), "Duplicate projectile observation in one tick");
        // No shot-start crop. Once selected, retain changed/missing owners too;
        // the provider must reject them rather than accept a shortened prefix.
        if !selected_owner && !self.tracks.contains_key(id) { return Ok(()); }
        ensure!(self.tracks.len() < MAX_TRACKS || self.tracks.contains_key(id), "Projectile track budget exceeded");
        ensure!(self.samples < MAX_ROWS, "Projectile snapshot budget exceeded");
        let track = self.tracks.entry(id.to_owned()).or_insert_with(|| json!({
            "id":id,"class":CLASS,"first_seen_tick":first,"removed_tick":null,"samples":[]}));
        ensure!(track["removed_tick"].is_null(), "Deleted projectile identity reappeared");
        let samples = track["samples"].as_array_mut().unwrap();
        ensure!(samples.len() < MAX_TRACK_ROWS, "Projectile per-track snapshot budget exceeded");
        ensure!(samples.last().is_none_or(|row| row["tick"].as_i64() == Some(tick as i64 - 1)),
            "Projectile history has an observation gap");
        samples.push(json!({"tick":tick,"active":active,"fields":fields})); self.samples += 1;
        Ok(())
    }

    pub fn finish_tick(&mut self, tick: i32) -> Result<()> {
        ensure!(self.open_tick == Some(tick), "Projectile finish differs from open tick");
        for (id, track) in &mut self.tracks {
            if !self.present.contains(id) && track["removed_tick"].is_null() { track["removed_tick"] = json!(tick); }
        }
        self.present.clear(); self.open_tick = None; Ok(())
    }

    fn coverage(&self, end: i32) -> Value {
        let reason = self.failure.or_else(|| if self.last_tick != Some(end) || self.open_tick.is_some() { Some("end_tick_not_observed") } else { None });
        json!({"availability":if reason.is_none(){"available"}else{"unavailable"},"reason":reason,
            "origin":"signon","class":CLASS,"first_observed_tick":self.first_tick,"last_observed_tick":self.last_tick,
            "tracks":self.tracks.len(),"samples":self.samples,
            "scope":"Selected-owner network projectile history, including pre-shot rows and later owner changes. No client visibility, interpolation or post-deletion tail inference."})
    }

    pub fn summary(&self, end: i32) -> Value {
        let c = self.coverage(end);
        json!({"schema_version":1,"policy":POLICY,"file":FILE,"availability":c["availability"],
            "reason":c["reason"],"tracks":self.tracks.len(),"samples":self.samples})
    }

    fn write_payload<W: Write>(&self, out: &mut W, start: i32, end: i32) -> Result<()> {
        let header = json!({"schema_version":1,"policy":POLICY,
            "requested_range":{"start_tick":start,"end_tick":end,"inclusive":true},"coverage":self.coverage(end)});
        let mut head = serde_json::to_vec(&header)?;
        ensure!(head.pop() == Some(b'}'), "Invalid projectile output header");
        out.write_all(&head)?; out.write_all(b",\"tracks\":[")?;
        for (index, row) in self.tracks.values().enumerate() {
            if index != 0 { out.write_all(b",")?; }
            serde_json::to_writer(&mut *out, row)?;
        }
        out.write_all(b"]}\n")?; Ok(())
    }

    fn prepare_publication(&mut self, start: i32, end: i32, budget: usize) -> Result<()> {
        // Count the exact serialized payload before touching the reserved file.
        // Only a data-budget failure becomes unavailable. Real output I/O,
        // competing files and failed flush/sync remain fatal to extraction.
        let mut count = Limited { inner: io::sink(), bytes: 0, limit: budget, exceeded: false };
        if let Err(error) = self.write_payload(&mut count, start, end) {
            if !count.exceeded { return Err(error); }
            eprintln!("AGR: projectile capture unavailable: serialized payload exceeds budget");
            self.invalidate("serialized_budget_exceeded");
        }
        Ok(())
    }

    pub fn write_new(&mut self, path: &Path, start: i32, end: i32) -> Result<()> {
        self.prepare_publication(start, end, MAX_JSON)?;
        let file = OpenOptions::new().write(true).create_new(true).open(path)?;
        let mut out = Limited { inner: BufWriter::new(file), bytes: 0, limit: MAX_JSON, exceeded: false };
        self.write_payload(&mut out, start, end)?;
        out.flush()?; out.inner.get_ref().sync_all()?; Ok(())
    }

}

struct Limited<W> { inner: W, bytes: usize, limit: usize, exceeded: bool }
impl<W: Write> Write for Limited<W> {
    fn write(&mut self, data: &[u8]) -> io::Result<usize> {
        if data.len() > self.limit - self.bytes { self.exceeded = true; return Err(io::Error::other("Projectile JSON budget exceeded")); }
        let size = self.inner.write(data)?; self.bytes += size; Ok(size)
    }
    fn flush(&mut self) -> io::Result<()> { self.inner.flush() }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn observe(c: &mut Capture, tick: i32, selected: bool) {
        c.begin_tick(tick, 10).unwrap();
        c.observe_fields("7:2", tick, true, selected, BTreeMap::from([("m_hOwnerEntity".into(),json!(if selected {1} else {2}))])).unwrap();
        c.finish_tick(tick).unwrap();
    }
    #[test]
    fn preserves_prerange_and_changed_owner_observations() {
        let mut c = Capture::default(); observe(&mut c, 0, true); observe(&mut c, 1, false);
        assert_eq!(c.tracks["7:2"]["first_seen_tick"], json!(0));
        assert_eq!(c.tracks["7:2"]["samples"][1]["fields"]["m_hOwnerEntity"], json!(2));
        assert_eq!(c.summary(1)["availability"], json!("available"));
    }
    #[test]
    fn earlier_unselected_prefix_is_explicitly_missing() {
        let mut c = Capture::default(); observe(&mut c, 0, false); observe(&mut c, 1, true);
        assert_eq!(c.tracks["7:2"]["first_seen_tick"], json!(0));
        assert_eq!(c.tracks["7:2"]["samples"][0]["tick"], json!(1));
    }
    #[test]
    fn no_observations_differs_from_observed_empty() {
        let mut c = Capture::default(); assert_eq!(c.summary(1)["availability"], json!("unavailable"));
        c.begin_tick(1, 1).unwrap(); c.finish_tick(1).unwrap();
        assert_eq!(c.summary(1)["availability"], json!("available")); assert!(c.tracks.is_empty());
    }
    #[test]
    fn deleted_identity_cannot_reappear_and_gaps_cannot_be_hidden() {
        let mut c = Capture::default(); observe(&mut c, 0, true);
        c.begin_tick(1, 3).unwrap(); c.finish_tick(1).unwrap(); c.begin_tick(2, 3).unwrap();
        assert!(c.observe_fields("7:2", 2, true, false, BTreeMap::new()).is_err());
        let mut c = Capture::default(); observe(&mut c, 0, true); c.begin_tick(2, 3).unwrap();
        assert!(c.observe_fields("7:2", 2, true, true, BTreeMap::new()).is_err());
    }
    #[test]
    fn snapshot_budget_refuses_before_appending() {
        let mut c = Capture::default(); c.begin_tick(0, 1).unwrap(); c.samples = MAX_ROWS;
        assert!(c.observe_fields("7:2", 0, true, true, BTreeMap::new()).is_err()); assert!(c.tracks.is_empty());
    }
    #[test]
    fn publication_is_exclusive_and_bounded() {
        let path = std::env::temp_dir().join(format!("projectile-exclusive-{}.json",std::process::id()));
        fs::write(&path,b"prior").unwrap(); let mut c = Capture::default();
        assert!(require_fresh(&path).is_err()); assert!(c.write_new(&path,0,1).is_err());
        assert_eq!(fs::read(&path).unwrap(),b"prior"); fs::remove_file(&path).unwrap();
        let mut out = Limited { inner: Vec::new(), bytes: MAX_JSON, limit: MAX_JSON, exceeded: false };
        assert!(out.write_all(b"x").is_err()); assert!(out.inner.is_empty());
    }
    #[test]
    fn failed_capture_discards_prefix_and_never_retries() {
        let mut c = Capture::default(); observe(&mut c, 0, true);
        c.samples = MAX_ROWS;
        c.capture_tick(1, 3, |capture| capture.observe_fields("7:2", 1, true, true, BTreeMap::new()));
        let coverage = c.summary(3);
        assert_eq!(coverage["availability"], json!("unavailable"));
        assert_eq!(coverage["reason"], json!("capture_failed"));
        assert_eq!(coverage["samples"], json!(0)); assert_eq!(coverage["tracks"], json!(0));
        assert!(c.first.is_empty() && c.present.is_empty() && c.tracks.is_empty() && c.open_tick.is_none());
        c.capture_tick(2, 3, |_| panic!("failed optional capture must not retry"));
        assert_eq!(c.summary(3), coverage);
    }
    #[test]
    fn row_and_track_limits_use_the_same_unavailable_transition() {
        let mut c = Capture::default(); observe(&mut c, 0, true);
        c.tracks.get_mut("7:2").unwrap()["samples"] = json!((0..MAX_TRACK_ROWS).map(|t|json!({"tick":t})).collect::<Vec<_>>());
        c.capture_tick(1, 2, |capture| capture.observe_fields("7:2", 1, true, true, BTreeMap::new()));
        assert_eq!(c.failure, Some("capture_failed")); assert!(c.tracks.is_empty());
        let mut c = Capture::default();
        for i in 0..MAX_TRACKS { c.tracks.insert(format!("{i}:0"),json!({})); }
        c.capture_tick(1, 2, |capture| capture.observe_fields("9000:0", 1, true, true, BTreeMap::new()));
        assert_eq!(c.failure, Some("capture_failed")); assert!(c.tracks.is_empty());
    }
    #[test]
    fn serialized_data_budget_is_checked_before_output_creation() {
        let mut c = Capture::default();
        for tick in 0..20 {
            c.capture_tick(tick, 19, |capture| capture.observe_fields("7:2", tick, true, true,
                BTreeMap::from([("m_hOwnerEntity".into(),json!(1))])));
        }
        assert_eq!(c.summary(19)["availability"], json!("available"));
        c.prepare_publication(0,19,1024).unwrap();
        assert_eq!(c.failure, Some("serialized_budget_exceeded")); assert!(c.tracks.is_empty());
        let path=std::env::temp_dir().join(format!("projectile-unavailable-{}.json",std::process::id()));
        require_fresh(&path).unwrap(); c.write_new(&path,0,19).unwrap();
        let value: Value=serde_json::from_slice(&fs::read(&path).unwrap()).unwrap(); fs::remove_file(&path).unwrap();
        assert_eq!(value["coverage"]["reason"],json!("serialized_budget_exceeded"));
        assert_eq!(value["tracks"],json!([])); assert_eq!(value["coverage"]["samples"],json!(0));
        assert!(value["coverage"]["tracks"]==json!(0));
    }
    #[test]
    fn optional_capture_does_not_hide_output_errors_or_observed_empty() {
        let mut c = Capture::default(); c.capture_tick(0,0,|_|Ok(()));
        assert_eq!(c.summary(0)["availability"], json!("available"));
        let path=std::env::temp_dir().join(format!("projectile-output-failure-{}",std::process::id()));
        fs::create_dir(&path).unwrap(); assert!(c.write_new(&path,0,0).is_err()); fs::remove_dir(&path).unwrap();
        assert_eq!(c.failure,None);
    }

}
