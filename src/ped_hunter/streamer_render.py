"""Backends for the ACTUAL shared scene. Offscreen requires existing Pillow.
No Tk root/native capture/browser/subprocess is created by rasterize().
"""
from __future__ import annotations
import math
from pathlib import Path

FONT=Path('C:/Windows/Fonts/segoeui.ttf')
BOLD=Path('C:/Windows/Fonts/segoeuib.ttf')

def rounded(xy,r=16):
    x,y,x2,y2=xy;r=min(r,(x2-x)/2,(y2-y)/2)
    points=[]
    for cx,cy,start in ((x2-r,y+r,-90),(x2-r,y2-r,0),(x+r,y2-r,90),(x+r,y+r,180)):
        for a in range(start,start+91,15):
            points.extend((cx+r*math.cos(math.radians(a)),cy+r*math.sin(math.radians(a))))
    return points

def fitted(item,scale,measure):
    size=max(1,round(item.size*scale))
    minimum=max(1,round(16*scale))
    text=item.text
    budget=item.budget*scale
    while size>minimum and measure(text,size)>budget:
        size-=1
    # Setup/context are deliberately truncated; numeric readings are never clipped.
    truncated=False
    if measure(text,size)>budget and item.target.startswith(('setup:','context')):
        while text and measure(text+'…',size)>budget:
            text=text[:-1]
        text+='…';truncated=True
    return text,size,truncated,measure(text,size)<=budget+.5

def rasterize(scene,path=None):
    from PIL import Image,ImageDraw,ImageFont
    image=Image.new('RGB',(scene.width,scene.height),scene.items[0].fill)
    draw=ImageDraw.Draw(image)
    cache={}
    def font(size,bold):
        key=(size,bold)
        if key not in cache:cache[key]=ImageFont.truetype(str(BOLD if bold else FONT),size)
        return cache[key]
    s=scene.scale;audit=[]
    def segment(xy,fill,width,dash):
        if not dash:draw.line(xy,fill=fill,width=width);return
        x,y,x2,y2=xy;length=math.hypot(x2-x,y2-y)
        if length==0:return
        pos=0;n=0
        while pos<length:
            stop=min(length,pos+dash[n%len(dash)]*s)
            if n%2==0:draw.line((x+(x2-x)*pos/length,y+(y2-y)*pos/length,x+(x2-x)*stop/length,y+(y2-y)*stop/length),fill=fill,width=width)
            pos=stop;n+=1
    for i in scene.items:
        xy=tuple(v*s for v in i.xy);width=max(1,round(i.width*s))
        if i.kind=='text':
            measure=lambda t,z:draw.textlength(t,font=font(z,i.bold))
            text,size,truncated,fits=fitted(i,s,measure)
            span=measure(text,size)
            left=xy[0]-(span/2 if i.anchor=='n' else span if i.anchor=='ne' else 0)
            draw.text((left,xy[1]),text,font=font(size,i.bold),fill=i.fill,anchor='lt')
            if i.underline:
                yy=xy[1]+size+2*s
                draw.line((left,yy,left+span,yy),fill=i.fill,width=max(1,round(s)))
            audit.append(dict(text=text,source=i.text,target=i.target,fontPixels=size,fits=fits,truncated=truncated,
                              fontFile=str(BOLD if i.bold else FONT),fontBackend='Pillow/FreeType explicit file',
                              bounds=[*xy,xy[0]+measure(text,size),xy[1]+size+3*s]))
        elif i.kind=='line':segment(xy,i.fill,width,i.dash)
        elif i.kind=='ellipse':draw.ellipse(xy,fill=i.fill or None,outline=i.outline or None,width=width)
        elif i.kind in ('polygon','round'):
            coords=rounded(xy,16*s) if i.kind=='round' else xy
            pairs=list(zip(coords[::2],coords[1::2]));draw.polygon(pairs,fill=i.fill or None)
            if i.outline:
                if i.dash:
                    for a,b in zip(pairs,pairs[1:]+pairs[:1]):segment((*a,*b),i.outline,width,i.dash)
                else:draw.line(pairs+[pairs[0]],fill=i.outline,width=width)
        else:
            draw.rectangle(xy,fill=i.fill or None,outline=None if i.dash else i.outline or None,width=width)
            if i.dash and i.outline:
                x,y,x2,y2=xy
                for line in ((x,y,x2,y),(x2,y,x2,y2),(x2,y2,x,y2),(x,y2,x,y)):
                    segment(line,i.outline,width,i.dash)
    if path is not None:image.save(path)
    return image,audit

def paint_canvas(canvas,scene):
    """No listeners here; window binds once. Replace all drawing in one UI callback."""
    import tkinter.font as tkfont
    canvas.delete('all');fonts=[];s=scene.scale
    canvas.configure(background=scene.items[0].fill)
    for i in scene.items:
        xy=tuple(v*s for v in i.xy)
        tags=((i.target,) if i.target else ())+(('affordance:'+i.affordance,) if i.affordance else ())
        kw=dict(fill=i.fill,tags=tags)
        if i.kind=='text':
            cache={}
            def font(z):
                if z not in cache:cache[z]=tkfont.Font(canvas,family='Segoe UI',size=-z,weight='bold' if i.bold else 'normal',underline=i.underline)
                return cache[z]
            text,size,_,_=fitted(i,s,lambda t,z:font(z).measure(t))
            fonts.append(font(size));canvas.create_text(*xy,text=text,font=fonts[-1],anchor=i.anchor,**kw)
        elif i.kind=='line':canvas.create_line(*xy,width=max(1,i.width*s),dash=tuple(max(1,round(v*s)) for v in i.dash),**kw)
        else:
            opts=dict(outline=i.outline,width=max(1,i.width*s),**kw)
            if i.dash:opts['dash']=tuple(max(1,round(v*s)) for v in i.dash)
            if i.kind=='ellipse':canvas.create_oval(*xy,**opts)
            elif i.kind=='round':canvas.create_polygon(*rounded(xy,16*s),**opts)
            elif i.kind=='polygon':canvas.create_polygon(*xy,**opts)
            else:canvas.create_rectangle(*xy,**opts)
    canvas._streamer_fonts=fonts
    # Semantic records retain complete names/state independently of fitted text.
    # This is not a claim that the native Canvas exposes a screen-reader role.
    canvas._streamer_controls=scene.controls
    canvas._streamer_font_request={'family':'Segoe UI','backend':'Tk font family request; native resolution unqualified'}
