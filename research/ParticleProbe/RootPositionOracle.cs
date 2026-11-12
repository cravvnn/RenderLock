#if AGR_SIMULATION_HITBOX
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using ValveResourceFormat.Particles;

static class RootPositionOracle
{
    public static void Check(string output)
    {
        var flags=BindingFlags.Instance|BindingFlags.NonPublic;
        var write=typeof(ParticleSystemState).GetMethod("WriteAuthoredRootPosition",flags)!;
        var legacy=typeof(ParticleSystemState).GetMethod("WriteAuthoredChildPosition",flags)!;
        int trees=0,rejections=0;
        foreach(int cp in new[]{0,1})
        foreach(bool local in new[]{false,true})
        {
            var root=SetChildControlOracle.Create();var child=SetChildControlOracle.Create(root.RenderState);
            var grandchild=SetChildControlOracle.Create(child.RenderState);
            SetChildControlOracle.Attach(root,child);SetChildControlOracle.Attach(child,grandchild);
            var nodes=new[]{root,child,grandchild};ControlPointAudit.BeginInputs(root,[0,1]);
            for(int i=0;i<nodes.Length;i++)
            foreach(int index in new[]{0,1})
            {
                var p=local?SetChildControlOracle.OwnPoint(nodes[i],index):nodes[i].RenderState.GetControlPoint(index);
                p.Position=new(i+1,i+2,i+3);p.Rotation=Quaternion.CreateFromAxisAngle(Vector3.UnitY,.3f*(i+1));
                typeof(ControlPoint).GetProperty("PositionPrevious")!.SetValue(p,new Vector3(-1-i,-2-i,-3-i));
            }
            var before=nodes.Select(n=>new[]{n.RenderState.GetControlPoint(0),n.RenderState.GetControlPoint(1)}).ToArray();
            var rotations=before.Select(points=>points.Select(p=>p.GetRotation()).ToArray()).ToArray();
            var prior=before.Select(points=>points.Select(p=>p.PositionPrevious).ToArray()).ToArray();
            var other=before.Select(points=>points[1-cp].Position).ToArray();
            var priorUnprovided=root.RenderState.UnprovidedControlPointIndices;
            foreach(var position in new[]{new Vector3(9,8,7),new Vector3(5,6,7)})
            {
                write.Invoke(root.RenderState,[cp,position]);
                for(int i=0;i<nodes.Length;i++)
                foreach(int index in new[]{0,1})
                {
                    var p=nodes[i].RenderState.GetControlPoint(index);
                    if(!ReferenceEquals(p,before[i][index])||p.GetRotation()!=rotations[i][index]||p.PositionPrevious!=prior[i][index]
                        ||p.Position!=(index==cp?position:other[i]))throw new Exception("Root position write changed another CP, basis, history or identity");
                }
            }
            foreach(var method in new[]{write,legacy})
            {
                try{method.Invoke(child.RenderState,[cp,Vector3.Zero]);throw new Exception("Child guard removed");}
                catch(TargetInvocationException e)when(e.InnerException is InvalidOperationException){rejections++;}
            }
            if(!root.RenderState.AuthoredControlPointIndices.SequenceEqual(new[]{cp})
                ||!root.RenderState.UnprovidedControlPointIndices.SequenceEqual(priorUnprovided))throw new Exception("Wrong CP provenance");
            trees++;
        }
        File.WriteAllText(output,JsonSerializer.Serialize(new{status="PASS",trees,rejected_child_writes=rejections,
            scope="Actual root writer, inherited and existing local child/grandchild points, identity, CP isolation, orientation and previous-position preservation. Native setter tested separately."},new JsonSerializerOptions{WriteIndented=true}));
        Console.WriteLine("Root position oracle PASS");
    }
}

#endif
