//! 离线样本与区块抽样共用的 MT19937；整数种子、53 位实数和拒绝采样遵循 CPython。
//! 仅用于可重复研究，不用于安全凭据。
pub(crate) struct Random { mt: [u32; 624], index: usize, normal: Option<f64> }
impl Random {
    pub(crate) fn new(seed: u64) -> Self {
        let mut r = Self { mt: [0; 624], index: 624, normal: None }; r.mt[0] = 19650218;
        for i in 1..624 { r.mt[i] = 1812433253_u32.wrapping_mul(r.mt[i-1] ^ (r.mt[i-1] >> 30)).wrapping_add(i as u32); }
        let key = if seed > u32::MAX as u64 { vec![seed as u32, (seed >> 32) as u32] } else { vec![seed as u32] };
        let (mut i, mut j) = (1, 0);
        for _ in 0..624 {
            r.mt[i] = (r.mt[i] ^ ((r.mt[i-1] ^ (r.mt[i-1] >> 30)).wrapping_mul(1664525))).wrapping_add(key[j]).wrapping_add(j as u32);
            i += 1; j += 1; if i >= 624 { r.mt[0] = r.mt[623]; i = 1; } if j >= key.len() { j = 0; }
        }
        for _ in 0..623 {
            r.mt[i] = (r.mt[i] ^ ((r.mt[i-1] ^ (r.mt[i-1] >> 30)).wrapping_mul(1566083941))).wrapping_sub(i as u32);
            i += 1; if i >= 624 { r.mt[0] = r.mt[623]; i = 1; }
        }
        r.mt[0] = 0x80000000; r
    }
    fn next(&mut self) -> u32 {
        if self.index >= 624 {
            for i in 0..624 { let y = (self.mt[i] & 0x80000000) | (self.mt[(i+1)%624] & 0x7fffffff); self.mt[i] = self.mt[(i+397)%624] ^ (y >> 1) ^ if y & 1 != 0 { 0x9908b0df } else { 0 }; }
            self.index = 0;
        }
        let mut y = self.mt[self.index]; self.index += 1; y ^= y >> 11; y ^= (y << 7) & 0x9d2c5680; y ^= (y << 15) & 0xefc60000; y ^= y >> 18; y
    }
    pub(crate) fn range(&mut self, n: usize) -> usize {
        assert!(n > 0, "empty random range");
        let bits = usize::BITS - n.leading_zeros();
        loop { let value = if bits <= 32 { (self.next() >> (32-bits)) as usize } else { let low = self.next() as u64; let high = (self.next() >> (64-bits)) as u64; (low | (high << 32)) as usize }; if value < n { return value; } }
    }
    fn unit(&mut self) -> f64 { let a = self.next() >> 5; let b = self.next() >> 6; (a as f64 * 67108864.0 + b as f64) / 9007199254740992.0 }
    pub(crate) fn gauss(&mut self, mean: f64, sigma: f64) -> f64 {
        let z = if let Some(value) = self.normal.take() { value } else {
            let angle = self.unit() * std::f64::consts::TAU; let radius = (-2.0 * (1.0-self.unit()).ln()).sqrt();
            self.normal = Some(angle.sin() * radius); angle.cos() * radius
        }; mean + sigma * z
    }
    pub(crate) fn log_normal(&mut self, mean: f64, sigma: f64) -> f64 {
        let scale = 4.0 * (-0.5_f64).exp() / 2.0_f64.sqrt();
        loop { let a = self.unit(); let b = 1.0-self.unit(); let z = scale * (a-0.5) / b; if z*z/4.0 <= -b.ln() { return (mean+sigma*z).exp(); } }
    }
}
