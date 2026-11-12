#if AGR_SIMULATION_HITBOX
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using ValveKeyValue;
using ValveResourceFormat.Particles;

static class LiteralIdentityControlOracle
{
    static object Create(int cp,Vector3 position,string? transform,bool setOnce=false)
    {
        var d=KVObject.Collection();d["_class"]=new("C_OP_SetSingleControlPointPosition");d["m_nCP1"]=new(cp);
        d["m_bSetOnce"]=new(setOnce);
        var v=KVObject.Collection();v["m_nType"]=new("PVEC_TYPE_LITERAL");v["m_vLiteralValue"]=KVObject.Array([new(position.X),new(position.Y),new(position.Z)]);d["m_vecCP1Pos"]=v;
        if(transform is not null){var t=KVObject.Collection();t["m_nType"]=new(transform);d["m_transformInput"]=t;}
        var factory=typeof(ParticleSystemSimulation).Assembly.GetType("ValveResourceFormat.Particles.ParticleControllerFactory",true)!;
        object?[] args=["C_OP_SetSingleControlPointPosition",d,NullLogger.Instance,12,null];
        if(!(bool)factory.GetMethod("TryCreatePreEmissionOperator")!.Invoke(null,args)!)throw new Exception("Registered literal writer unavailable");
        return args[^1]!;
    }
    static void Apply(object op,ParticleSystemState state)
    {object[] args=[state,1/64f];op.GetType().GetMethod("Operate")!.Invoke(op,args);}
    public static void Check(string output)
    {
        var root=SetChildControlOracle.Create();var child=SetChildControlOracle.Create(root.RenderState);var grand=SetChildControlOracle.Create(child.RenderState);
        SetChildControlOracle.Attach(root,child);SetChildControlOracle.Attach(child,grand);
        var nodes=new[]{root,child,grand};var before=new List<(ControlPoint cp,Quaternion rotation,Vector3 previous)>();
        for(int i=0;i<nodes.Length;i++)foreach(var index in new[]{3,4})
        {
            var cp=i==0?nodes[i].RenderState.GetControlPoint(index):SetChildControlOracle.OwnPoint(nodes[i],index);
            cp.Rotation=Quaternion.CreateFromAxisAngle(Vector3.UnitY,.1f*(i+1));typeof(ControlPoint).GetProperty("PositionPrevious")!.SetValue(cp,new Vector3(-i-1,-2,-3));
            before.Add((cp,cp.GetRotation(),cp.PositionPrevious));
        }
        ControlPointAudit.BeginInputs(root,[0,1,2]);root.MainControlPoint.Position=new(1000,200,30);root.MainControlPoint.Rotation=Quaternion.Identity;
        var relative=Create(3,new(0,0,120),null);var literal=Create(4,new(1.5f,0,0),"PT_TYPE_INVALID");
        for(int run=0;run<2;run++)
        {
            Apply(relative,root.RenderState);Apply(literal,root.RenderState);
            foreach(var node in nodes)
            {
                if(node.RenderState.GetControlPoint(3).Position!=new Vector3(1000,200,150)
                    ||node.RenderState.GetControlPoint(4).Position!=new Vector3(1.5f,0,0))throw new Exception("Literal/relative root writer mismatch");
            }
            foreach(var (cp,rotation,previous)in before)
                if(cp.GetRotation()!=rotation||cp.PositionPrevious!=previous)throw new Exception("Literal root writer changed basis/history");
        }
        var state=root.RenderState;
        if(!state.AuthoredControlPointIndices.SequenceEqual(new[]{3,4})||!state.AuthoredRootPositionIndices.SequenceEqual(new[]{3,4})
            ||state.UnprovidedControlPointIndices.Length!=0||state.ControlPointInputPolicy!="agr_authored_root_position_v4")throw new Exception("Literal root writer lacks exact provenance");
        var unknown=SetChildControlOracle.Create();ControlPointAudit.BeginInputs(unknown,[0]);
        Apply(Create(4,new(1.5f,0,0),"PT_TYPE_NAMED_VALUE"),unknown.RenderState);
        if(!unknown.RenderState.UnprovidedControlPointIndices.SequenceEqual(new[]{4})||unknown.RenderState.AuthoredControlPointIndices.Contains(4))
            throw new Exception("Unknown identity fallback was granted provenance");
        var once=Create(3,new(9,8,7),"PT_TYPE_INVALID",true);
        Apply(once,root.RenderState);root.RenderState.GetControlPoint(3).Position=new(21);
        Apply(once,root.RenderState);
        if(root.RenderState.GetControlPoint(3).Position!=new Vector3(21))throw new Exception("Root set-once reapplied");
        once.GetType().GetMethod("Reset")!.Invoke(once,[]);Apply(once,root.RenderState);
        if(root.RenderState.GetControlPoint(3).Position!=new Vector3(9,8,7))throw new Exception("Root reset did not reapply");
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",trees=3,writes=12,unknown_provider_unqualified=true,set_once_and_reset_preserved=true,
            basis_and_history_preserved=true,scope="Actual registered literal PT_TYPE_INVALID/default CP transform writers through root/child/grandchild. Native transform/position setter arithmetic separately pinned."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine("Registered literal identity control writer PASS");
    }
}

#endif
