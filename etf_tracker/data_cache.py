#!/usr/bin/env python3
"""
数据缓存管理器 - 提升数据获取速度和稳定性
功能：
1. 本地 CSV/Parquet 缓存 K 线数据
2. 按日期失效，避免盘中重复拉取
3. 缓存元数据管理（来源、更新时间、数据条数）
4. 数据一致性校验
"""

import os
import json
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional, Dict
import warnings
warnings.filterwarnings('ignore')


class DataCache:
    """本地数据缓存管理器"""
    
    def __init__(self, cache_dir: str = None):
        self.cache_dir = cache_dir or os.path.expanduser("~/etf_tracker/data/cache")
        self.meta_path = os.path.join(self.cache_dir, "cache_meta.json")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.meta = self._load_meta()
    
    def _load_meta(self) -> Dict:
        """加载缓存元数据"""
        if os.path.exists(self.meta_path):
            try:
                with open(self.meta_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    
    def _save_meta(self):
        """保存缓存元数据"""
        with open(self.meta_path, 'w', encoding='utf-8') as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2)
    
    def _get_cache_path(self, code: str, data_type: str = "kline") -> str:
        """获取缓存文件路径"""
        safe_code = code.replace('.', '_')
        return os.path.join(self.cache_dir, f"{data_type}_{safe_code}.parquet")
    
    def _get_meta_key(self, code: str, data_type: str = "kline") -> str:
        """获取元数据 key"""
        return f"{data_type}_{code}"
    
    def get_cache(self, code: str, data_type: str = "kline", max_age_days: int = 1) -> Optional[pd.DataFrame]:
        """
        获取缓存数据
        
        Args:
            code: 标的代码
            data_type: 数据类型 (kline, stock)
            max_age_days: 缓存最大有效期（天），默认1天
        
        Returns:
            缓存数据 DataFrame，如果过期或不存在则返回 None
        """
        cache_path = self._get_cache_path(code, data_type)
        meta_key = self._get_meta_key(code, data_type)
        
        if not os.path.exists(cache_path) or meta_key not in self.meta:
            return None
        
        # 检查缓存是否过期
        last_update = self.meta[meta_key].get('last_update', '')
        if not last_update:
            return None
        
        try:
            last_dt = datetime.fromisoformat(last_update)
            age = (datetime.now() - last_dt).total_seconds() / 86400
            if age > max_age_days:
                print(f"  [Cache] {code} 缓存已过期 ({age:.1f} 天)，重新获取")
                return None
        except Exception:
            return None
        
        # 读取缓存
        try:
            df = pd.read_parquet(cache_path)
            # 确保 date 列为 datetime 类型
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
            print(f"  [Cache] {code} 命中缓存 ({len(df)} 条, {last_update})")
            return df
        except Exception as e:
            print(f"  [Cache] {code} 读取缓存失败: {e}")
            return None
    
    def save_cache(self, code: str, df: pd.DataFrame, data_type: str = "kline", source: str = "unknown"):
        """
        保存数据到缓存
        
        Args:
            code: 标的代码
            df: 数据 DataFrame
            data_type: 数据类型
            source: 数据来源
        """
        if df is None or len(df) == 0:
            return
        
        cache_path = self._get_cache_path(code, data_type)
        meta_key = self._get_meta_key(code, data_type)
        
        # 确保数据类型正确
        df_to_save = df.copy()
        if 'date' in df_to_save.columns:
            df_to_save['date'] = pd.to_datetime(df_to_save['date'])
        
        # 保存为 parquet（高效、压缩）
        df_to_save.to_parquet(cache_path, index=False, compression='zstd')
        
        # 更新元数据
        self.meta[meta_key] = {
            'code': code,
            'data_type': data_type,
            'last_update': datetime.now().isoformat(),
            'source': source,
            'rows': len(df_to_save),
            'columns': list(df_to_save.columns),
            'start_date': df_to_save['date'].iloc[0].strftime('%Y-%m-%d') if 'date' in df_to_save.columns else None,
            'end_date': df_to_save['date'].iloc[-1].strftime('%Y-%m-%d') if 'date' in df_to_save.columns else None
        }
        self._save_meta()
        print(f"  [Cache] {code} 缓存已保存 ({len(df_to_save)} 条, 来源: {source})")
    
    def is_fresh(self, code: str, data_type: str = "kline", max_age_hours: float = 6) -> bool:
        """检查缓存是否新鲜（默认 6 小时内）"""
        meta_key = self._get_meta_key(code, data_type)
        if meta_key not in self.meta:
            return False
        
        last_update = self.meta[meta_key].get('last_update', '')
        if not last_update:
            return False
        
        try:
            last_dt = datetime.fromisoformat(last_update)
            age_hours = (datetime.now() - last_dt).total_seconds() / 3600
            return age_hours <= max_age_hours
        except Exception:
            return False
    
    def clear_cache(self, code: str = None, data_type: str = None):
        """清理缓存"""
        if code and data_type:
            cache_path = self._get_cache_path(code, data_type)
            if os.path.exists(cache_path):
                os.remove(cache_path)
            meta_key = self._get_meta_key(code, data_type)
            self.meta.pop(meta_key, None)
        elif code:
            for f in os.listdir(self.cache_dir):
                if f.endswith('.parquet') and code.replace('.', '_') in f:
                    os.remove(os.path.join(self.cache_dir, f))
            keys_to_remove = [k for k in self.meta if k.endswith(f"_{code}")]
            for k in keys_to_remove:
                self.meta.pop(k, None)
        else:
            # 清理所有 parquet
            for f in os.listdir(self.cache_dir):
                if f.endswith('.parquet'):
                    os.remove(os.path.join(self.cache_dir, f))
            self.meta = {}
        
        self._save_meta()
        print(f"[Cache] 缓存已清理")
    
    def get_cache_stats(self) -> Dict:
        """获取缓存统计"""
        total_size = 0
        total_files = 0
        for f in os.listdir(self.cache_dir):
            if f.endswith('.parquet'):
                fp = os.path.join(self.cache_dir, f)
                total_size += os.path.getsize(fp)
                total_files += 1
        
        return {
            'files': total_files,
            'size_mb': round(total_size / 1024 / 1024, 2),
            'entries': len(self.meta)
        }


if __name__ == "__main__":
    # 测试
    cache = DataCache()
    print(cache.get_cache_stats())
