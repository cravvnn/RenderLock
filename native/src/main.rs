//! Read-only replay extraction. Entity state is replayed from signon; only output is cropped.
use std::{collections::{BTreeMap, HashSet}, fs::{self, File}, io::{BufWriter, Write}, path::Path};
use anyhow::{Context as _, ensure};
use boon_proto::proto::{CDemoFullPacket, CDemoPacket, CnetMsgTick};
use prost::Message;
use serde_json::{Value, json};

const ANIM: &str = "CBodyComponent.m_animationController.";
const SKEL: &str = "CBodyComponent.m_skeletonInstance.";
const HERO: &str = "m_CCitadelHeroComponent.m_spawnedHero.m_nHeroID";

fn encoded_field(name: &str, value: &boon::FieldValue) -> serde_json::Result<Value> {
    match value {
        boon::FieldValue::String(bytes) if name.ends_with(".m_topology") => Ok(json!(bytes)),
        _ => serde_json::to_value(value),
    }
}

fn dynamic_bytes(mut bytes: BTreeMap<usize, Value>, length: usize) -> Vec<Value> {
    (0..length).map(|i| bytes.remove(&i).unwrap_or(Value::Null)).collect()
}

fn snapshot(entity: &boon::Entity, serializer: &boon::Serializer, tick: i32, hero: i64) -> anyhow::Result<Value> {
    let recipe_prefix = format!("{ANIM}m_SerializePoseRecipeAG2Dynamic.");
    let mut fields = BTreeMap::<String, Value>::new();
    let mut bytes = BTreeMap::<usize, Value>::new();
    for (key, value) in &entity.fields {
        let Some(name) = serializer.field_name_for_key(*key) else { continue };
        if let Some(suffix) = name.strip_prefix(&recipe_prefix) {
            if let Ok(i) = suffix.parse::<usize>() { bytes.insert(i, serde_json::to_value(value)?); }
        } else if name.starts_with(ANIM) || name.starts_with(SKEL) ||
            ["m_angEyeAngles", "m_iHealth", "m_iMaxHealth", "m_lifeState", "m_flDeathTime",
             "m_hController", "m_hPawn", "m_flSimulationTime", "m_flAnimTime",
             "m_clrRender", "m_nRenderMode"].contains(&name.as_str()) {
            // Topology is binary despite its network string representation.
            // FieldValue's generic JSON conversion replaces invalid UTF-8.
            let encoded = encoded_field(&name, value)?;
            fields.insert(name, encoded);
        }
    }
    // Dynamic vectors can shrink; stale indexed fields must not extend them.
    let array_len = fields.get(&format!("{ANIM}m_SerializePoseRecipeAG2Dynamic"))
        .and_then(Value::as_u64).unwrap_or(0) as usize;
    let recipe_bytes = dynamic_bytes(bytes, array_len);
    // Boon now resolves display names through an external catalog. Preserve
    // IDs offline; naming must never block recorded entity extraction.
    Ok(json!({"demo_tick":tick,"hero_id":hero,"hero_name":format!("Hero {hero}"),
        "entity_id":format!("{}:{}",entity.index,entity.serial),"entity_index":entity.index,"entity_serial":entity.serial,
        "active":entity.active,"fields":fields,"serialized_pose_recipe_array":recipe_bytes}))
}

fn recipe_tick(row: &Value) -> Option<u32> {
    let bytes = row["serialized_pose_recipe_array"].as_array()?;
    if bytes.len() < 4 { return None; }
    let prefix: Option<Vec<u8>> = bytes[..4].iter().map(|v| v.as_u64().and_then(|x| u8::try_from(x).ok())).collect();
    Some(u32::from_le_bytes(prefix?.try_into().ok()?))
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    ensure!(args.len() == 5 || args.len() == 6,
        "Usage: agr-extract DEM OUTPUT START_TICK END_TICK [HERO_ID,HERO_ID,...]");
    let start: i32 = args[3].parse().context("START_TICK must be an integer")?;
    let end: i32 = args[4].parse().context("END_TICK must be an integer")?;
    ensure!(start >= 0 && start <= end && end < i32::MAX, "Invalid inclusive tick range");
    let heroes: HashSet<i64> = if args.len() == 6 {
        args[5].split(',').map(str::parse).collect::<Result<_, _>>()?
    } else { HashSet::new() };
    ensure!(heroes.iter().all(|h| *h > 0), "Hero IDs must be positive");
    let output = Path::new(&args[2]);
    fs::create_dir_all(output)?;
    for name in ["raw_animation_state.json", "network_ticks.json", "native_coverage.json"] {
        ensure!(!output.join(name).exists(), "Refusing to overwrite {}", output.join(name).display());
    }
    let parser = boon::Parser::from_file(Path::new(&args[1]))?;
    parser.verify()?;
    let observed_ticks = parser.distinct_ticks()?;
    ensure!(observed_ticks.iter().any(|t| *t >= start && *t <= end), "No recorded ticks in requested range");
    ensure!(observed_ticks.last().is_some_and(|t| end <= *t), "End tick is beyond the recorded stream");

    // Preserve every clock observation through END_TICK. Recipe headers may refer
    // to a server tick from before START_TICK. No assumed fixed clock offset.
    let data = fs::read(&args[1])?;
    let demo = pbdems2::demo::Demo::new(&data)?;
    let mut body = Vec::new();
    let mut payload = Vec::new();
    let mut clocks = BTreeMap::new();
    for frame in demo.commands() {
        let frame = frame?;
        let header = frame.header();
        if header.tick > end { continue; }
        if ![7, 8, 13].contains(&header.cmd) { continue; }
        frame.decode_body(&mut body)?;
        let packet = if header.cmd == 13 {
            CDemoFullPacket::decode(body.as_slice())?.packet
        } else { Some(CDemoPacket::decode(body.as_slice())?) };
        let Some(packet) = packet else { continue };
        for message in pbdems2::packet::PacketMessageIter::new(&packet.data.unwrap_or_default()) {
            let message = message?;
            if message.message_type() == 4 {
                message.copy_payload(&mut payload)?;
                if let Some(server_tick) = CnetMsgTick::decode(payload.as_slice())?.tick {
                    clocks.insert(header.tick, server_tick);
                }
            }
        }
    }
    drop(data);
    let clock_rows: Vec<_> = clocks.iter().map(|(d,s)| json!({"demo_tick":d,"server_tick":s})).collect();
    serde_json::to_writer(BufWriter::new(File::create(output.join("network_ticks.json"))?), &clock_rows)?;

    let filter = HashSet::from(["CCitadelPlayerPawn", "CCitadelGameRulesProxy"]);
    let mut writer = BufWriter::new(File::create(output.join("raw_animation_state.json"))?);
    writer.write_all(b"[\n")?;
    let mut count = 0usize;
    let mut empty_recipes = 0usize;
    let mut gaps = 0usize;
    let mut dormant_samples = 0usize;
    let mut seen = HashSet::new();
    let mut warmup_requests = BTreeMap::<i32, Vec<(i32,u32,i64)>>::new();
    let mut unresolved_warmup = Vec::new();
    let mut callback_error: Option<anyhow::Error> = None;
    eprintln!("AGR: reconstructing entity state from signon through tick {end}");
    parser.decode_segment(None, end + 1, &filter, |ctx| {
        if ctx.tick() < start || ctx.tick() > end || callback_error.is_some() { return; }
        let result = (|| -> anyhow::Result<()> {
            for (index, entity) in ctx.entities().iter() {
                if entity.class_name.as_ref() != "CCitadelPlayerPawn" { continue; }
                let Some(serializer) = ctx.serializers().get(&entity.class_name) else { continue };
                let hero = entity.get_by_name(HERO, serializer).and_then(|v| serde_json::to_value(v).ok())
                    .and_then(|v| v.as_i64()).unwrap_or(0);
                if hero <= 0 || (!heroes.is_empty() && !heroes.contains(&hero)) { continue; }
                // Retain dormant state explicitly. The importer can hide it; it
                // must not mistake last-known positions for fresh network poses.
                if !entity.active { dormant_samples += 1; }
                let row = snapshot(entity, serializer, ctx.tick(), hero)?;
                let bytes = row["serialized_pose_recipe_array"].as_array().unwrap();
                gaps += bytes.iter().filter(|v| v.is_null()).count();
                if bytes.is_empty() { empty_recipes += 1; }
                if seen.insert(entity.id()) {
                    if let Some(update) = recipe_tick(&row) {
                        if clocks.get(&ctx.tick()).is_some_and(|current| update < *current) {
                            if let Some((&seed_tick, _)) = clocks.range(..start).rev().find(|(_,s)| **s == update) {
                                warmup_requests.entry(seed_tick).or_default().push((index, entity.serial, hero));
                            } else { unresolved_warmup.push(format!("{index}:{} update {update} has no preceding clock",entity.serial)); }
                        }
                    }
                }
                if count > 0 { writer.write_all(b",\n")?; }
                serde_json::to_writer(&mut writer, &row)?;
                count += 1;
            }
            Ok(())
        })();
        if let Err(error) = result { callback_error = Some(error); }
    })?;
    if let Some(error) = callback_error.take() { return Err(error); }
    // A stale first sample needs the actual last update snapshot, including its
    // animation context, not a copy of the current stale buffer. Recover only
    // those seed ticks in a second complete from-signon state pass.
    let mut warmup_count = 0usize;
    if let Some((&last_seed, _)) = warmup_requests.last_key_value() {
        eprintln!("AGR: recovering fresh animation seeds before tick {start}");
        parser.decode_segment(None, last_seed + 1, &filter, |ctx| {
            let Some(requests) = warmup_requests.get(&ctx.tick()) else { return; };
            if callback_error.is_some() { return; }
            let result = (|| -> anyhow::Result<()> {
                for &(index, serial, hero) in requests {
                    let Some((_, entity)) = ctx.entities().iter().find(|(i,e)| *i == index && e.serial == serial) else {
                        unresolved_warmup.push(format!("{index}:{serial} not present at seed tick {}",ctx.tick()));
                        continue;
                    };
                    let Some(serializer) = ctx.serializers().get(&entity.class_name) else { continue; };
                    let mut row = snapshot(entity, serializer, ctx.tick(), hero)?;
                    if recipe_tick(&row) != clocks.get(&ctx.tick()).copied() {
                        unresolved_warmup.push(format!("{index}:{serial} is not fresh at seed tick {}",ctx.tick()));
                        continue;
                    }
                    row["warmup"] = json!(true);
                    writer.write_all(b",\n")?;
                    serde_json::to_writer(&mut writer, &row)?;
                    warmup_count += 1;
                }
                Ok(())
            })();
            if let Err(error) = result { callback_error = Some(error); }
        })?;
    }
    if let Some(error) = callback_error { return Err(error); }
    writer.write_all(b"\n]\n")?;
    writer.flush()?;
    ensure!(count > 0, "No matching hero pawn samples in requested tick range");
    let coverage = json!({"adapter":"agr-extract 0.2.0","boon_version":"0.15.0",
        "state_reconstruction":"from_signon", "tick_range_inclusive":true,
        "source_world_half":32768,
        "identity":"entity_index_and_serial", "pawn_samples":count,
        "raw_ag2_empty_samples":empty_recipes,"raw_ag2_missing_array_indices":gaps,
        "animation_warmup_samples":warmup_count,"unresolved_animation_warmup":unresolved_warmup,
        "dormant_samples":dormant_samples,"network_tick_samples":clock_rows.len(),
        "particles":"not_extracted", "map":"not_extracted", "ragdoll":"not_simulated"});
    serde_json::to_writer_pretty(BufWriter::new(File::create(output.join("native_coverage.json"))?), &coverage)?;
    eprintln!("AGR: saved {count} pawn snapshots and {} network clocks", clock_rows.len());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn topology_preserves_non_utf8_bytes() {
        let raw = boon::FieldValue::String(vec![0, 0xff, 0x80, 0x42]);
        assert_eq!(encoded_field("slots.2.m_topology", &raw).unwrap(), json!([0,255,128,66]));
    }
    #[test]
    fn dynamic_vector_uses_declared_length_and_preserves_gaps() {
        let values = BTreeMap::from([(0,json!(12)), (2,json!(255)), (9,json!(99))]);
        assert_eq!(dynamic_bytes(values.clone(), 3), vec![json!(12), Value::Null, json!(255)]);
        assert_eq!(dynamic_bytes(values, 0), Vec::<Value>::new());
    }
}
