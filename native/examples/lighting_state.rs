//! Offline CCitadelGaffer sunlight state; no client/game execution.
use std::{collections::{BTreeMap, HashSet}, fs::OpenOptions, io::{BufWriter, Write}, path::Path};
use anyhow::{ensure, Context as _};
use serde_json::{json, Value};

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    ensure!(args.len() == 4, "Usage: lighting_state DEM NEW_OUTPUT_JSON TICK");
    let tick: i32 = args[3].parse().context("Tick must be an integer")?;
    ensure!(tick >= 0 && tick < i32::MAX, "Invalid tick");
    ensure!(!Path::new(&args[2]).exists(), "Preserve previous evidence; choose a new output");
    let parser = boon::Parser::from_file(Path::new(&args[1]))?;
    parser.verify()?;
    let classes = parser.parse_class_info()?;
    let filter: HashSet<&str> = classes.classes().iter().map(|c| c.network_name.as_ref()).filter(|name| name.contains("Gaffer")).collect();
    ensure!(!filter.is_empty(), "No Gaffer class in replay class info");
    let init = parser.parse_init()?;
    let mut schemas = Vec::new();
    for class in classes.classes().iter().filter(|c| filter.contains(c.network_name.as_ref())) {
        let serializer = init.serializers().get(&class.table_name).or_else(|| init.serializers().get(&class.network_name));
        let fields: Vec<_> = serializer.into_iter().flat_map(|s| s.fields.iter()).enumerate().map(|(index, f)| json!({
            "index":index,"name":f.var_name,"type":f.var_type,
            "nested_serializer":f.field_serializer_name,"send_node":f.send_node,
            "bit_count":f.bit_count,"low":f.low_value,"high":f.high_value
        })).collect();
        schemas.push(json!({"class_id":class.class_id,"network_name":class.network_name,
            "table_name":class.table_name,"serializer_present":serializer.is_some(),"fields":fields,
            "signon_baseline_bytes":init.string_tables().instance_baseline(class.class_id).map(|v|v.len())}));
    }
    let mut tick_baselines = Vec::new();
    let mut rows = Vec::new();
    let mut counts = BTreeMap::<String, usize>::new();
    let mut field_names = HashSet::new();
    let mut completed = false;
    let mut error = None;
    parser.decode_segment(None, tick + 1, &filter, |ctx| {
        if ctx.tick() != tick || error.is_some() { return; }
        completed = true;
        tick_baselines = classes.classes().iter().filter(|c| filter.contains(c.network_name.as_ref()))
            .map(|c|json!({"class_id":c.class_id,"baseline_bytes":ctx.string_tables().instance_baseline(c.class_id).map(|v|v.len())})).collect();
        let result = (|| -> anyhow::Result<()> {
            for (_, entity) in ctx.entities().iter() {
                *counts.entry(entity.class_name.to_string()).or_default() += 1;
                let serializer = ctx.serializers().get(&entity.class_name)
                    .context("Materialized entity has no serializer")?;
                let mut fields = BTreeMap::<String, Value>::new();
                for (key, value) in &entity.fields {
                    let Some(name) = serializer.field_name_for_key(*key) else { continue; };
                    {
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
    let result = json!({"status":if rows.is_empty() {"NO_MATERIALIZED_ENTITY"} else {"EXTRACTED"},"matched_classes":filter.iter().collect::<Vec<_>>(),"demo":args[1],"tick":tick,
        "state_reconstruction":"from_signon","class_filter":"recorded Gaffer classes",
        "class_counts":counts,"declared_schemas":schemas,"tick_baselines":tick_baselines,"field_names":names,"entities":rows,
        "scope":"Network Gaffer state only; engine uniform mapping and nonnetwork state are not asserted."});
    let file = OpenOptions::new().write(true).create_new(true).open(&args[2])?;
    let mut writer = BufWriter::new(file);
    serde_json::to_writer_pretty(&mut writer, &result)?;
    writer.flush()?;
    writer.get_ref().sync_all()?;
    println!("Extracted {} Gaffer entities at tick {}", rows.len(), tick);
    Ok(())
}
