//! Bounded offline node fields for one exact pawn lifetime; no game execution.
use std::{collections::BTreeMap, fs::OpenOptions, io::{BufWriter, Write}, path::Path};
use anyhow::{ensure, Context as _};
use serde_json::{json, Value};

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    ensure!(args.len() == 6, "Usage: particle_scene_node_history DEM NEW_JSON START END INDEX:SERIAL");
    let start: i32 = args[3].parse()?;
    let end: i32 = args[4].parse()?;
    ensure!(start >= 0 && end >= start && end-start <= 256 && end < i32::MAX, "Invalid bounded range");
    ensure!(!Path::new(&args[2]).exists(), "Choose a new evidence file");
    let parser = boon::Parser::from_file(Path::new(&args[1]))?;
    parser.verify()?;
    let init = parser.parse_init()?;
    let mut schemas = Vec::new();
    let mut local_scale_declarations = Vec::new();
    for (name, serializer) in init.serializers().iter() {
        for field in &serializer.fields {
            if field.var_name == "m_flClientLocalScale" {
                local_scale_declarations.push(name.to_string());
            }
        }
        if name == "CGameSceneNode" || name == "CSkeletonInstance" {
            schemas.push(json!({"name":name,"fields":serializer.fields.iter().map(|f|json!({
                "name":f.var_name,"type":f.var_type,"nested_serializer":f.field_serializer_name
            })).collect::<Vec<_>>()}));
        }
    }
    schemas.sort_by_key(|row| row["name"].as_str().unwrap().to_string());
    local_scale_declarations.sort();
    let mut rows = Vec::new();
    let mut error = None;
    parser.decode_segment(None, end+1, &["CCitadelPlayerPawn"].into(), |ctx| {
        if ctx.tick() < start || ctx.tick() > end || error.is_some() { return; }
        let result = (|| -> anyhow::Result<()> {
            for (_, entity) in ctx.entities().iter() {
                if format!("{}:{}", entity.index, entity.serial) != args[5] { continue; }
                let serializer = ctx.serializers().get(&entity.class_name).context("Missing pawn serializer")?;
                let mut fields = BTreeMap::<String, Value>::new();
                for (key, value) in &entity.fields {
                    let Some(name) = serializer.field_name_for_key(*key) else { continue; };
                    if name.starts_with("CBodyComponent.m_skeletonInstance.")
                        && !name.contains(".m_modelState.") {
                        fields.insert(name, serde_json::to_value(value)?);
                    }
                }
                rows.push(json!({"tick":ctx.tick(),"entity_id":args[5],"active":entity.active,"fields":fields}));
            }
            Ok(())
        })();
        if let Err(value) = result { error = Some(value); }
    })?;
    if let Some(value) = error { return Err(value); }
    ensure!(rows.len() == (end-start+1) as usize, "Exact pawn history missing samples");
    let result = json!({"status":"EXTRACTED","demo":args[1],"start_tick":start,"end_tick":end,
        "entity_id":args[5],"state_reconstruction":"from_signon", "node_schemas":schemas,
        "client_local_scale_declarations":local_scale_declarations,"rows":rows,
        "scope":"Network scene-node fields only; no client-local state/interpolation/pose is inferred."});
    let file = OpenOptions::new().write(true).create_new(true).open(&args[2])?;
    let mut writer = BufWriter::new(file);
    serde_json::to_writer_pretty(&mut writer, &result)?;
    writer.flush()?;
    writer.get_ref().sync_all()?;
    println!("Extracted {} exact pawn node samples", rows.len());
    Ok(())
}
