using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;

static class ReplayLifecycle
{
    // Return true for a terminal immediate destruction. The caller must retain
    // empty output frames afterward without advancing the destroyed tree.
    public static bool Destroy(ParticleSystemSimulation root, bool immediately)
    {
        root.Stop();
        if (!immediately)
        {
            root.PlayEndCap();
            return false;
        }

        void Clear(ParticleSystemSimulation system)
        {
            system.Particles.Clear();
            system.RenderState.ParticleCount = 0;
            foreach (var child in system.Children)
            {
                Clear(child);
            }
        }
        Clear(root);
        return true;
    }

    public static void Check(string output)
    {
        ParticleSystemSimulation Create(bool instantaneous = false, ParticleSystemState? parent = null)
        {
            var emitter = KVObject.Collection();
            emitter["_class"] = new KVObject(instantaneous ? "C_OP_InstantaneousEmitter" : "C_OP_ContinuousEmitter");
            emitter[instantaneous ? "m_nParticlesToEmit" : "m_flEmitRate"] = new KVObject(instantaneous ? 1 : 64);
            var decay = KVObject.Collection();
            decay["_class"] = new KVObject("C_OP_Decay");
            var definition = KVObject.Collection();
            definition["_class"] = new KVObject("CParticleSystemDefinition");
            definition["m_nBehaviorVersion"] = new KVObject(12);
            definition["m_flConstantLifespan"] = new KVObject(.25f);
            definition["m_Emitters"] = KVObject.Array([emitter]);
            definition["m_Operators"] = KVObject.Array([decay]);
            return new ParticleSystemSimulation(ParticleSystem.Create(definition), null!,
                NullLogger.Instance, parentSystemState: parent);
        }

        (ParticleSystemSimulation Root, ParticleSystemSimulation Normal, ParticleSystemSimulation Cap, List<SeedBinding> Seeds) Tree()
        {
            var root = Create();
            var normal = Create(parent: root.RenderState);
            var cap = Create(instantaneous: true, parent: root.RenderState);
            var childrenField = typeof(ParticleSystemSimulation).GetField("childSimulations", BindingFlags.Instance | BindingFlags.NonPublic)
                ?? throw new Exception("Pinned child simulation contract changed");
            var capField = typeof(ParticleSystemSimulation).GetField("isEndCapChild", BindingFlags.Instance | BindingFlags.NonPublic)
                ?? throw new Exception("Pinned endcap child contract changed");
            if (childrenField.GetValue(root) is not List<ParticleSystemSimulation> children)
            {
                throw new Exception("Invalid pinned child list");
            }
            children.Add(normal);
            children.Add(cap);
            capField.SetValue(cap, true);
            var seeds = SeedBinding.Apply(root, 12345, "lifecycle-oracle");
            root.Start();
            for (var tick = 0; tick < 4; tick++)
            {
                root.Update(1f / 64f, tick / 64f);
            }
            if (root.Particles.Count == 0 || normal.Particles.Count == 0 || cap.Particles.Count != 0)
            {
                throw new Exception("Lifecycle oracle did not create normal particles and an idle endcap");
            }
            return (root, normal, cap, seeds);
        }

        var delayed = Tree();
        var rootIds = delayed.Root.Particles.Current.ToArray().Select(p => p.UniqueParticleId).ToHashSet();
        var childIds = delayed.Normal.Particles.Current.ToArray().Select(p => p.UniqueParticleId).ToHashSet();
        if (Destroy(delayed.Root, false) || delayed.Root.Particles.Count != rootIds.Count || delayed.Normal.Particles.Count != childIds.Count)
        {
            throw new Exception("Non-immediate destroy discarded existing particles");
        }
        var restored = SeedBinding.RestoreEndCapSeeds(delayed.Root, delayed.Seeds);
        if (restored.Count != 1 || restored[0].Path != "root/1") throw new Exception("Endcap seed restore touched an unrelated system");
        delayed.Root.Update(1f / 64f, 4f / 64f);
        foreach (var seed in delayed.Seeds) seed.Verify();
        var survivingRoot = delayed.Root.Particles.Count;
        var survivingChild = delayed.Normal.Particles.Count;
        var endcapParticles = delayed.Cap.Particles.Count;
        if (survivingRoot == 0 || survivingChild == 0 || endcapParticles != 1)
        {
            throw new Exception("Destroy did not preserve tails and start the endcap");
        }
        for (var tick = 5; tick < 40; tick++)
        {
            delayed.Root.Update(1f / 64f, tick / 64f);
            foreach (var seed in delayed.Seeds) seed.Verify();
            foreach (var particle in delayed.Root.Particles.Current)
            {
                if (!rootIds.Contains(particle.UniqueParticleId)) throw new Exception("Root emitted after destroy");
            }
            foreach (var particle in delayed.Normal.Particles.Current)
            {
                if (!childIds.Contains(particle.UniqueParticleId)) throw new Exception("Normal child emitted after destroy");
            }
        }
        if (delayed.Root.Particles.Count != 0 || delayed.Normal.Particles.Count != 0 || delayed.Cap.Particles.Count != 0)
        {
            throw new Exception("Stopped particle tails did not expire through their authored decay");
        }
        var immediate = Tree();
        if (!Destroy(immediate.Root, true) || immediate.Root.Particles.Count != 0
            || immediate.Normal.Particles.Count != 0 || immediate.Cap.Particles.Count != 0)
        {
            throw new Exception("Immediate destroy retained particles");
        }
        File.WriteAllText(output, JsonSerializer.Serialize(new
        {
            status = "PASS", surviving_root_after_stop = survivingRoot, surviving_child_after_stop = survivingChild,
            endcap_particles_after_stop = endcapParticles, normal_emission_stopped = true,
            tails_expired = true, immediate_tree_cleared = true,
            explicit_endcap_substitute_seed_restore = true,
            scope = "Actual pinned simulator public lifecycle; synthetic root/normal/endcap tree uses explicit private child adapters. No installed-effect rendering or engine parity claim."
        }, new JsonSerializerOptions { WriteIndented = true }));
    }
}
