#if AGR_SIMULATION_SHADER_EXTRAS
using System.Text.Json;
using ValveKeyValue;
using ValveResourceFormat.Particles;
using ValveResourceFormat.ResourceTypes;
using Microsoft.Extensions.Logging.Abstractions;

static class ShaderExtraFields
{
    public static void Check(string output)
    {
        KVObject Literal(float value)
        {
            var result=KVObject.Collection();result["m_nType"]=new("PF_TYPE_LITERAL");
            result["m_flLiteralValue"]=new(value);return result;
        }
        var init=KVObject.Collection();init["_class"]=new("C_INIT_InitFloat");
        init["m_nOutputField"]=new(39);init["m_InputValue"]=Literal(1);
        var set=KVObject.Collection();set["_class"]=new("C_OP_SetFloat");
        set["m_nOutputField"]=new(39);set["m_InputValue"]=Literal(1);
        set["m_nSetMethod"]=new("PARTICLE_SET_ADD_TO_INITIAL_VALUE");set["m_Lerp"]=Literal(.5f);
        var read=KVObject.Collection();read["_class"]=new("C_OP_SetFloat");read["m_nOutputField"]=new(40);
        var input=KVObject.Collection();input["m_nType"]=new("PF_TYPE_PARTICLE_FLOAT");
        input["m_nScalarAttribute"]=new(39);input["m_nMapType"]=new("PF_MAP_TYPE_MULT");input["m_flMultFactor"]=new(3f);
        read["m_InputValue"]=input;
        var emitter=KVObject.Collection();emitter["_class"]=new("C_OP_InstantaneousEmitter");emitter["m_nParticlesToEmit"]=new(1);
        var definition=KVObject.Collection();definition["_class"]=new("CParticleSystemDefinition");
        definition["m_nBehaviorVersion"]=new(12);
        definition["m_Initializers"]=KVObject.Array([init]);definition["m_Emitters"]=KVObject.Array([emitter]);
        definition["m_Operators"]=KVObject.Array([set,read]);
        var sim=new ParticleSystemSimulation(ParticleSystem.Create(definition),null!,NullLogger.Instance);
        sim.Start();sim.Update(1e-6f,0);
        if(sim.Particles.Count!=1)throw new Exception("Shader attribute oracle emitted no particle");
        var p=sim.Particles.Current[0];
        if(p.ShaderExtraData1!=1.5f||p.ShaderExtraData2!=4.5f)
            throw new Exception($"Shader attribute initialization/set/read failed: {p.ShaderExtraData1}, {p.ShaderExtraData2}");
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",shader_extra_data1=p.ShaderExtraData1,
            shader_extra_data2=p.ShaderExtraData2,scope="Actual initializer, initial-value set method, lerp and chained scalar-provider storage; not engine visual parity"}));
    }
}

#endif
