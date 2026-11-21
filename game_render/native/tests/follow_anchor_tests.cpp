#include "dolly_follow_anchor.hpp"
#ifdef NDEBUG
#undef NDEBUG
#endif
#include <cassert>
#include <limits>

int main() {
    for (unsigned flags : {3u,7u,11u,15u}) assert(dolly::follow_anchor_flags(flags));
    for (unsigned flags : {0u,1u,2u,4u,8u,16u,19u}) assert(!dolly::follow_anchor_flags(flags));
    dolly::FollowDescriptor a{}, b{}, out{};
    for (unsigned i=0;i<16;++i) a[i]=b[i]=float(i+1);
    std::array<float,3> anchor{-8237,-1459,364};
    for (unsigned i=0;i<3;++i) a[3+i]=0, b[3+i]=anchor[i];
    const auto original_a=a, original_b=b;
    for (float weight : {.001f,.25f,.999f}) {
        assert(dolly::follow_anchor_copy(weight,true,a,b,anchor,out));
        for (unsigned i=0;i<16;++i)
            assert(out[i] == (i>=3 && i<6 ? b[i] : a[i]));
        for (unsigned i=0;i<3;++i) {
            float blended=(1-weight)*out[3+i]+weight*b[3+i];
            assert(std::abs(blended-anchor[i])<.001f);
        }
        assert(a==original_a && b==original_b);
    }
    for (float weight : {0.f,1.f,-1.f,2.f,std::numeric_limits<float>::quiet_NaN()})
        assert(!dolly::follow_anchor_copy(weight,true,a,b,anchor,out));
    assert(!dolly::follow_anchor_copy(.25f,false,a,b,anchor,out));
    a[3]=1; assert(!dolly::follow_anchor_copy(.25f,true,a,b,anchor,out)); a=original_a;
    b[4]+=1; assert(!dolly::follow_anchor_copy(.25f,true,a,b,anchor,out)); b=original_b;
    b[8]=std::numeric_limits<float>::infinity();
    assert(!dolly::follow_anchor_copy(.25f,true,a,b,anchor,out));
    a={}; b={}; anchor={};
    assert(!dolly::follow_anchor_copy(.25f,true,a,b,anchor,out));
}
