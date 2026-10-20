//! Offline network relationship inventory; client-created wearables may be absent.
use std::{collections::{BTreeMap, HashSet}, fs::OpenOptions, io::{BufWriter, Write}, path::Path};
use anyhow::{ensure, Context as _};
use serde_json::{json, Value};

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    ensure!(args.len() == 4, "Usage: particle_relationships DEM NEW_OUTPUT_JSON TICK");
    let tick: i32 = args[3].parse().context("Tick must be an integer")?;
    ensure!(tick >= 0 && tick < i32::MAX, "Invalid tick");
    ensure!(!Path::new(&args[2]).exists(), "Preserve previous evidence; choose a new output");
    let parser = boon::Parser::from_file(Path::new(&args[1]))?;
    parser.verify()?;
    let classes = parser.parse_class_info()?;
    let filter: HashSet<&str> = classes.classes().iter().map(|c| c.network_name.as_ref()).collect();
    let mut rows = Vec::new();
    let mut counts = BTreeMap::<String, usize>::new();
    let mut field_names = HashSet::new();
    let mut completed = false;
    let mut error = None;
    parser.decode_segment(None, tick + 1, &filter, |ctx| {
        if ctx.tick() != tick || error.is_some() { return; }
        completed = true;
        let result = (|| -> anyhow::Result<()> {
            for (_, entity) in ctx.entities().iter() {
                *counts.entry(entity.class_name.to_string()).or_default() += 1;
                let serializer = ctx.serializers().get(&entity.class_name)
                    .context("Materialized entity has no serializer")?;
                let mut fields = BTreeMap::<String, Value>::new();
                for (key, value) in &entity.fields {
                    let Some(name) = serializer.field_name_for_key(*key) else { continue; };
                    let lower = name.to_ascii_lowercase();
                    if ["parent", "owner", "follow", "attachment", "wearable", "modelname", "m_hmodel",
                        "cosmetic", "econ", "loadout", "inventory", "itemdef", "itemid",
                        "variant", "skin", "bodygroup"]
                        .iter().any(|word| lower.contains(word)) {
                        field_names.insert(name.clone());
                        fields.insert(name, serde_json::to_value(value)?);
                    }
                }
                if !fields.is_empty() {
                    rows.push(json!({"entity_id":format!("{}:{}",entity.index,entity.serial),
                        "entity_index":entity.index,"entity_serial":entity.serial,
                        "class":entity.class_name,"active":entity.active,"fields":fields}));
                }
            }
            Ok(())
        })();
        if let Err(e) = result { error = Some(e); }
    })?;
    if let Some(e) = error { return Err(e); }
    ensure!(completed, "Requested tick was not observed");
    rows.sort_by_key(|row| row["entity_index"].as_i64());
    let mut names: Vec<_> = field_names.into_iter().collect();
    names.sort();
    let result = json!({"status":"EXTRACTED","demo":args[1],"tick":tick,
        "state_reconstruction":"from_signon","class_filter":"all recorded classes",
        "class_counts":counts,"relationship_field_names":names,"entities":rows,
        "scope":"Network model/owner/parent/attachment fields only. Client-created scene children and native child ordering are not asserted."});
    let file = OpenOptions::new().write(true).create_new(true).open(&args[2])?;
    let mut writer = BufWriter::new(file);
    serde_json::to_writer_pretty(&mut writer, &result)?;
    writer.flush()?;
    writer.get_ref().sync_all()?;
    println!("Extracted {} entities with relationship fields at tick {}", rows.len(), tick);
    Ok(())
}
