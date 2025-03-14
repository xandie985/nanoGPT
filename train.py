import math
import inspect
from dataclasses import dataclass

import torch
import torch.nn as nn
import torch.nn.functional as F

class LayerNorm(nn.Module):
    """Layer Norm with an optional bias. PyTorch's doesnt support simply bias = False"""
    
    def __init__(self, ndim, bias):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(ndim))
        self.bias = nn.Parameter(torch.zeros(ndim)) if bias else None
    
    def forward(self, input):
        return F.layer_norm(input, self.weight.shape, self.bias, 1e-5)


class CasualSelfAttention(nn.Module):

    def __init__(self, config):
        super().__init__()
        assert config.n_embd % config.n_head == 0

        #key, query, value projetions for all heads, but in batch 
        self.c_attn = nn.Linear(config.n_embd, 3*config.n_embd, bias=config.bias)

        #output projection
        self.c_proj = nn.Linear(config.n_embd, config.n_embd, bias=config.bias)

        #regularization
        self.attn_dropout = nn.Dropout(config.dropout)
        self.resid_dropout = nn.Dropout(config.dropout)

        self.n_head = config.n_head
        self.n_embd = config.n_embd
        self.dropout = config.dropout

        #flash attention to make GPU go brrrrrrrrrr (supports in  pytorch >=2.0)
        self.flash = hasattr(torch.nn.functional, 'scaled_dot_product_attention')

        if not self.flash:
            print("WARNING: Using slow attention. Flash attention requires pytorch>=2.0")
            #causal mark to ensure that attention is only applied to the left in the input seq
            self.register_buffer("bias", torch.tril(torch.ones(config.block_size, config.block_size)).view(1,1, config.block_size, config.block_size))

    
    def forward(self, x):
        B,T,C = x.size() #batch size, seq len and embedding dimentionality (n_embd)

        #calculate query, key, values for all the heads in batch and move head forward to be the batch dim
        q,k,v = self.c_attn(x).split(self.n_embd, dim=2)
        k = k.view(B, T, self.n_head, C // self.n_head).transpose(1,2) # (B, nh, T, hs)
        q = q.view(B, T, self.n_head, C // self.n_head).transpose(1,2) # (B, nh, T, hs)
        v = v.view(B, T, self.n_head, C // self.n_head).transpose(1,2) # (B, nh, T, hs)

        if self.flash:
            y = torch.nn.functional.scaled_dot_product_attention(q,k,v, attn_mask = None, dropout_p = self.dropout if self.training else 0, is_causal= True)
        else:
            #manual implementation of attention
            att = (q @ k.transpose(-2,-1))*(1.0/math.sqrt(k.size(-1)))
            att = att.masked_fill(self.bias[:,:,:T,:T] == 0, float('-inf'))
            att = F.softmax(att, dim =-1)
            att = self.attn_dropout(att)
            y = att @ v # (B, nh, T,T) x (B, nh, T, hs) -> (B,nh, T, hs)
        y = y.transpose(1,2).contigous().view(B, T, C) #reassemble all head outputs side by side

        #output projection
        y = self.resid_dropout(self.c_proj(y))
        return y

