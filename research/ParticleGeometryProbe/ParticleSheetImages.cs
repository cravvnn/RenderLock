using System.Numerics;
using ValveResourceFormat.ResourceTypes;

// A frame can repeat the same image record for channel variants. Every record
// must be equivalent: retaining one is then independent of channel selection.
// This does not support distinct image/channel rectangles or alpha cropping.
static class ParticleSheetImages
{
    public static bool IsSingleImage(Texture.SpritesheetData.Sequence.Frame frame)
    {
        var images=frame.Images;
        if(images is null||images.Length is <1 or >4)return false;
        var first=images[0];
        bool Finite(Vector2 v)=>float.IsFinite(v.X)&&float.IsFinite(v.Y);
        if(first is null||!Finite(first.CroppedMin)||!Finite(first.CroppedMax)
            ||!Finite(first.UncroppedMin)||!Finite(first.UncroppedMax))return false;
        return images.All(image=>image is not null
            &&image.CroppedMin==first.CroppedMin&&image.CroppedMax==first.CroppedMax
            &&image.UncroppedMin==first.UncroppedMin&&image.UncroppedMax==first.UncroppedMax);
    }
}
