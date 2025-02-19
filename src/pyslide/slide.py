#!usr/bin/env python3

"""
slide.py: contains 1. Slide class 2. Annotations Class

Slide class: wrapper to openslide.OpenSlide class with addition of annotations

Annotation class: Parses annotation file output from:
    1.Qupath
    2.ImageJ
    3.ASAP
"""

import os
import glob
import json
import itertools
import xml.etree.ElementTree as ET

import numpy as np
import openslide
import cv2
import seaborn as sns
from matplotlib.path import Path
from openslide import OpenSlide
import pandas as pd
import seaborn as sns
from itertools import chain
import operator as op
from pyslide.util.utilities import mask2rgb
from PIL import Image

__author__='Gregory Verghese'
__email__='gregory.verghese@gmail.com'


class Slide(OpenSlide):
    """
    WSI object that enables annotation overlay wrapper around 
    openslide.OpenSlide class. Generates annotation mask.

    :param _slide_mask: ndarray mask representation
    :param dims dimensions of WSI
    :param name: string name
    :param draw_border: boolean to generate border based on annotations
    :param _border: list of border coordinates [(x1,y1),(x2,y2)]
    """
    MAG_FACTORS={0:1,1:2,2:4,3:8,4:16,5:32,6:64}
    MASK_SIZE=(2000,2000)

    #filter mask should be passed in at the specified mag level
    def __init__(self,
                 filename,
                 mag=0,
                 annotations=None,
                 annotations_path=None,
                 labels=None,
                 source=None,
                 filter_mask=None,
                 filter_mask_path=None):
        super().__init__(filename)

        self.mag=mag
        self.dims=self.dimensions
        self.name=os.path.basename(filename)[:-5]
        self._border=None
        if filter_mask is not None:
            self.filter_mask = filter_mask
        elif filter_mask_path is not None:
            self.filter_mask = cv2.imread(filter_mask_path,cv2.IMREAD_GRAYSCALE)
        else:
            self.filter_mask = None

        if annotations is not None:
            self.annotations=annotations
        elif annotations_path is not None:
            self.annotations=Annotations(annotations_path,
                                         source=source,
                                         labels=labels,
                                         encode=True
                                         )
        else:
            self.annotations=None

    @property
    def slide_mask(self):
       mask=self.generate_mask((Slide.MASK_SIZE))
       mask=mask2rgb(mask)

       return mask


    def set_filter_mask(self, mask=None, mask_path=None):
        if mask is not None:
            self.filter_mask = mask
        elif mask_path is not None:
            self.filter_mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
        else:
            print("no valid mask detected")
    

    def generate_mask(self, size=None):
        """
        Generates mask representation of annotations.

        :param size: tuple of mask dimensions
        :return: self._slide_mask ndarray. single channel
            mask with integer for each class
        """
        x, y = self.dims[0], self.dims[1]
        slide_mask=np.zeros((y, x), dtype=np.uint8)
        self.annotations.encode=True
        coordinates=self.annotations.annotations
        keys=sorted(list(coordinates.keys()))
        for k in keys:
            v = coordinates[k]
            v = [np.array(a) for a in v]
            cv2.fillPoly(slide_mask, v, color=k)
        if size is not None:
            slide_mask=cv2.resize(slide_mask, size)
        return slide_mask


    @staticmethod
    def resize_border(dim, factor=1, threshold=None, operator='=>'):
        """
        Resize and redraw annotations border. Useful to trim wsi 
        and mask to specific size

        :param dim: dimensions
        :param factor: border increments
        :param threshold: min/max size
        :param operator: threshold limit
        :return new_dims: new border dimensions [(x1,y1),(x2,y2)]
        """
        if threshold is None:
            threshold=dim

        operator_dict={'>':op.gt,'=>':op.ge,'<':op.lt,'=<':op.lt}
        operator=operator_dict[operator]
        multiples = [factor*i for i in range(100000)]
        multiples = [m for m in multiples if operator(m,threshold)]
        diff = list(map(lambda x: abs(dim-x), multiples))
        new_dim = multiples[diff.index(min(diff))]
        return new_dim


    #TODO: function will change with format of annotations
    #data structure accepeted
    def get_border(self,space=100):
        """
        Generate border around max/min annotation points
        :param space: gap between max/min annotation point and border
        :self._border: border dimensions [(x1,y1),(x2,y2)]
        """
        if self.annotations is None:
            self._border=[[0,self.dims[0]],[0,self.dims[1]]]
        else:
            coordinates=self.annotations.annotations
            coordinates = list(chain(*list(coordinates.values())))
            coordinates=list(chain(*coordinates))
            f=lambda x: (min(x)-space, max(x)+space)
            self._border=list(map(f, list(zip(*coordinates))))

        mag_factor=Slide.MAG_FACTORS[self.mag]
        f=lambda x: (int(x[0]/mag_factor),int(x[1]/mag_factor))
        self._border=list(map(f,self._border))

        return self._border


    #Need to do min size in terms of micrometers not pixels
    def detect_components(self,level_dims=6,num_component=None,min_size=None):
        """
        Find the largest section on the slide
        :param down_factor: 
        :return image: image containing contour around detected section
        :return self._border: [(x1,x2),(y1,y2)] around detected section
        """
        new_dims=self.level_dimensions[6]
        image=np.array(self.get_thumbnail(self.level_dimensions[6]))
        gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
        blur=cv2.bilateralFilter(np.bitwise_not(gray),9,100,100)
        _,thresh=cv2.threshold(blur,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        contours,_=cv2.findContours(thresh,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_NONE)

        if num_component is not None:
            idx=sorted([(cv2.contourArea(c),i) for i,c in enumerate(contours)])
            contours=[contours[i] for c, i in idx]
            contours=contours[-num_component:]

        if min_size is not None:
            contours=list(map(lambda x, y: cv2.contourArea(x),contours))
            contours=[c for c in contours if c>min_area]

        borders=[]
        components=[]
        image_new=image.copy()
        for c in contours:
            x,y,w,h = cv2.boundingRect(c)
            x_scale=self.dims[0]/new_dims[0]
            y_scale=self.dims[1]/new_dims[1]
            x1=round(x_scale*x)
            x2=round(x_scale*(x+w))
            y1=round(y_scale*y)
            y2=round(y_scale*(y-h))
            self._border=[(x1,x2),(y1,y2)]
            image_new=cv2.rectangle(image_new,(x,y),(x+w,y+h),(0,255,0),2)
            components.append(image_new)
            borders.append([(x1,x2),(y1,y2)])

        return components, borders 
    

    def generate_region(self, 
                        mag=0, 
                        x=None, 
                        y=None, 
                        x_size=None, 
                        y_size=None,
                        scale_border=False, 
                        factor=1, 
                        threshold=None, 
                        operator='=>'):
        """
        Extracts specific regions of the slide
        :param mag: magnification level
        :param x: min x coordinate
        :param y: min y coordinate
        :param x_size: x dim size 
        :param y_size: y dim size
        :param scale_border: resize border
        :param factor:
        :param threshold:
        :param operator:
        :return: extracted region (RGB ndarray)
        """
        if x is None:
            self.get_border()
            x, y = self._border        
        if x is not None:
            if isinstance(x,tuple):
                if x_size is None:
                    x_min, x_max=x
                    x_size=x_max-x_min
                elif x_size is not None:
                    x_min=x[0]
                    x_max=x_min+x_size
            elif isinstance(x,int):
                x_min=x
                x_max=x+x_size
        if y is not None:
            if isinstance(y,tuple):
                if y_size is None:
                    y_min, y_max=y
                    y_size=y_max-y_min
                elif y_size is not None:
                    y_min=y[0]
                    y_max=y_min+y_size
            elif isinstance(y,int):
                y_min=y
                y_max=y_min+y_size

        if scale_border:
            x_size = Slide.resize_border(x_size, factor, threshold, operator)
            y_size = Slide.resize_border(y_size, factor, threshold, operator)
        if (x_min+x_size)>self.dimensions[0]:
            x_size=self.dimensions[0]-x_min
        if (y_min+y_size)>self.dimensions[1]:
            y_size=self.dimensions[1]-y_min

        x_size_adj=int(x_size/Slide.MAG_FACTORS[mag])
        y_size_adj=int(y_size/Slide.MAG_FACTORS[mag])
        region=self.read_region((x_min,y_min),mag,(x_size_adj, y_size_adj))

        ##need to crop the mask to the correct region 

        ##need to scale up the mask to the correct size


        #set masked regions to white as easy to filter out later        
        #bg_color = (255,255,255)
        #region_arr = np.array(region)
        #region_arr[self.filter_mask == 0] = bg_color
        #region = Image.fromarray(region_arr)
        
        mask=self.generate_mask()[y_min:y_min+y_size,x_min:x_min+x_size]
        mask=cv2.resize(mask,(x_size_adj,y_size_adj))
        return np.array(region.convert('RGB')), mask

    def get_filtered_region(self,start,mag,size):
        
        region = self.read_region(start,mag,size)
        start_x = start[0]
        start_y = start[1] 
        if self.filter_mask is None:
            return region
        #print(self.filter_mask.shape)
        #need to scale mask to correct size - requested mag / existing mag
        #filter mask will already be stored at the self.mag level
        filter_mask = self.filter_mask
        if mag != self.mag:
            scaling_factor = Slide.MAG_FACTORS[self.mag]/Slide.MAG_FACTORS[mag]
            #print("scaling: ",scaling_factor)
            #print("original:",filter_mask.shape)
            filter_mask = cv2.resize(filter_mask,(0,0), fx=scaling_factor, fy=scaling_factor)
            #print("resized:",filter_mask.shape)
            start_x = int(start[0]*scaling_factor)
            start_y = int(start[1]*scaling_factor)
        #need to crop the filter mask to the requested region
        #print("start_x",start_x,start_x+size[0])
        #print("start_y",start_y,start_y+size[1])
        #print("filter mask shape",filter_mask.shape) 
        filter_mask_cropped = filter_mask[start_x:start_x+size[0],start_y:start_y+size[1]]
        
        if filter_mask_cropped.shape < size:
            # Pad the mask to the desired size
            print("cropped shape:",filter_mask_cropped.shape)    
            print("padding")
            padded_mask = np.pad(filter_mask_cropped, ((0, size[0]-filter_mask_cropped.shape[0]), (0, size[1]-filter_mask_cropped.shape[1])), 'constant', constant_values=0)
            print("padded mask shape",padded_mask.shape)

        else:
            # The mask is already the desired size or larger, so no padding is needed
            padded_mask = filter_mask_cropped

        #need to transpose the mask to apply bitwise and
        padded_mask = np.transpose(padded_mask) 
        filtered_region = cv2.bitwise_and(np.array(region),np.array(region),mask=padded_mask)
        filtered_region = cv2.cvtColor(filtered_region, cv2.COLOR_RGBA2RGB)
        #print(filtered_region.shape,padded_mask.shape)
        #set regions outside the mask to white instead of black
        filtered_region[padded_mask == 0] = (255, 255, 255)
        return filtered_region,padded_mask


    def save(self, path, size=(2000,2000), mask=False):
        """
        Save thumbnail of slide in image file format
        :param path:
        :param size:
        :param mask:
        """
        if mask:
            cv2.imwrite(path,self._slide_mask)
        else:
            image = self.get_thumbnail(size)
            image = image.convert('RGB')
            image = np.array(image)
            cv2.imwrite(path,image)


class Annotations():

    """
    Returns dictionary of coordinates of ROIs. Reads annotation 
    files in either xml and json format and returns a dictionary 
    containing x,y coordinates for each region of interest in the 
    annotation

    :param path: string path to annotation file
    :param annotation_type: file type
    :param labels: list of ROI names ['roi1',roi2']
    :param _annotations: dictonary with return files
                      {roi1:[[x1,y1],[x2,y2],...[xn,yn],...roim:[]}
    """
    def __init__(self, path, source,labels=[], encode=False):
        self.paths=path if isinstance(path,list) else [path]
        self.source=source
        self.labels=labels
        self.encode=encode
        self._annotations=None
        self._generate_annotations()

    def __repr__(self):
        numbers=[len(v) for k, v in self._annotations.items()]
        print(numbers)
        df=pd.DataFrame({"classes":self.labels,"number":numbers})
        return str(df)

    @property
    def keys(self):
        return list(self.annotations.keys())

    @property
    def values(self):
        return list(self.annotations.values())

    @property
    def annotations(self):
        if self.encode:
            annotations=self.encode_keys()
            self.encode=False
        else:
            annotations=self._annotations
        return annotations

    @property
    def class_key(self):
        if self.labels is None:
            self.labels=list(self._annotations.keys())
        class_key={l:i+1 for i, l in enumerate(self.labels)}
        return class_key

    @property
    def numbers(self):
        numbers=[len(v) for k, v in self._annotations.items()]
        return dict(zip(self.labels,numbers))


    def _generate_annotations(self):
        """
        Calls appropriate method for file type.
        return: annotations: dictionary of coordinates
        """
        self._annotations={}
        if not isinstance(self.paths,list):
            self._paths=[self.paths] 
        if self.source is not None:
            for p in self.paths:
                annotations=getattr(self,'_'+self.source)(p)
                for k, v in annotations.items():
                    if k in self._annotations:
                        self._annotations[k].append(v)
                    else:
                        self._annotations[k]=v
        if len(self.labels)>0:
            self._annotations=self.filter_labels(self.labels)
        else:
            self.labels=list(self._annotations.keys())
        

    def filter_labels(self, labels):
        """
        remove labels from annotations
        :param labels: label list to remove
        :return annotations: filtered annotation dictionary
        """
        self.labels=labels
        keys = list(self._annotations.keys())
        for k in keys:
            if k not in labels:
                del self._annotations[k]
        return self._annotations


    def rename_labels(self,names):
        """
        rename annotation labels
        :param names: dictionary {current_labels:new_labels}
        """
        for k,v in names.items():
            self._annotations[v]=self._annotations.pop(k)
        self.labels=list(self._annotations.keys())        
    

    def encode_keys(self):
        """
        encode labels as integer values
        """
        annotations={self.class_key[k]: v for k,v in self._annotations.items()}
        return annotations


    def _imagej(self,path):
        """
        Parses xml files
        :param path:
        :return annotations: dict of coordinates
        """
        tree=ET.parse(path)
        root=tree.getroot()
        anns=root.findall('Annotation')
        labels=list(root.iter('Annotation'))
        labels=list(set([i.attrib['Name'] for i in labels]))
        #self.labels.extend(labels)
        annotations={l:[] for l in labels}
        for i in anns:
            label=i.attrib['Name']
            instances=list(i.iter('Vertices'))
            for j in instances:
                coordinates=list(j.iter('Vertex'))
                coordinates=[[c.attrib['X'],c.attrib['Y']] for c in coordinates]
                coordinates=[[round(float(c[0])),round(float(c[1]))] for c in coordinates]
                annotations[label]=annotations[label]+[coordinates]
        return annotations


    def _asap(self,path):
        """
        Parses _asap files
        :param path:
        :return annotations: dict of coordinates
        """
        tree=ET.parse(path)
        root=tree.getroot()
        ns=root[0].findall('Annotation')
        labels=list(root.iter('Annotation'))
        labels=list(set([i.attrib['PartOfGroup'] for i in labels]))
        annotations={l:[] for l in labels}
        for i in ns:
            coordinates=list(i.iter('Coordinate'))
            coordinates=[[float(c.attrib['X']),float(c.attrib['Y'])] for c in coordinates]
            coordinates=[[round(c[0]),round(c[1])] for c in coordinates]
            label=i.attrib['PartOfGroup']
            annotations[label]=annotations[label]+[coordinates]
        #annotations = {self.class_key[k]: v for k,v in annotations.items()}
        return annotations


    def _qupath(self,path):
        """
        Parses qupath annotation json files
        :param path: json file path
        :return annotations: dictionary of annotations
        """
        annotations={}
        with open(path) as json_file:
            j=json.load(json_file)
        for a in j:
            c=a['properties']['classification']['name']
            geometry=a['geometry']['type']
            coordinates=a['geometry']['coordinates']
            if c not in annotations:
                annotations[c]=[]
            if geometry=="LineString":
                points=[[int(i[0]),int(i[1])] for i in coordinates]
                annotations[c].append(points)
            elif geometry=="Polygon":  
                for a2 in coordinates:
                    points=[[int(i[0]),int(i[1])] for i in a2]
                    annotations[c].append(points)
            elif geometry=="MultiPolygon":
                for a2 in coordinates:
                    for a3 in a2:
                        points=[[int(i[0]),int(i[1])] for i in a3]
                        annotations[c].append(points)
        return annotations


    def _json(self,path):
        """
        Parses json file with following structure.
        :param path:
        :return annotations: dict of coordinates
        """
        with open(path) as json_file:
            json_annotations=json.load(json_file)
        
        labels=list(json_annotations.keys())
        self.labels.extend(labels) 
        annotations = {k: [[[int(i['x']), int(i['y'])] for i in v2] 
                       for v2 in v.values()] for k, v in json_annotations.items()}
        return annotations


    def _dataframe(self):
        """
        Parses dataframe with following structure
        """ 
        anns_df=pd.read_csv(path)
        anns_df.fillna('undefined', inplace=True)
        anns_df.set_index('labels',drop=True,inplace=True)
        self.labels=list(set(anns_df.index))
        annotations={l: list(zip(anns_df.loc[l].x,anns_df.loc[l].y)) for l in
                     self.labels}

        annotations = {self.class_key[k]: v for k,v in annotations.items()}
        self._annotations=annotations


    def _csv(self,path):
        """
        Parses csv file with following structure
        :param path: 
        :return annotations: dict of coordinates
        """
        anns_df=pd.read_csv(path)
        anns_df.fillna('undefined', inplace=True)
        anns_df.set_index('labels',drop=True,inplace=True)
        labels=list(set(anns_df.index))
        annotations={l: list(zip(anns_df.loc[l].x,anns_df.loc[l].y)) for l in
                     labels}

        #annotations = {self.class_key[k]: v for k,v in annotations.items()}
        self._annotations=annotations
        return annotations


    def df(self):
        """
        Returns dataframe of annotations.
        :return :dataframe of annotations
        """
        #key={v:k for k,v in self.class_key.items()}
        labels=[[l]*len(self._annotations[l][0]) for l in self._annotations.keys()]
        labels=list(chain(*labels))
        #labels=[key[l] for l in labels]
        x_values=[xi[0] for x in list(self._annotations.values()) for xi in x[0]]
        y_values=[yi[1] for y in list(self._annotations.values()) for yi in y[0]]
        df=pd.DataFrame({'labels':list(labels),'x':x_values,'y':y_values})

        return df


    def save(self,save_path):
        """
        Save down annotations in csv file.
        :param save_path: path to save annotations
        """
        self.df().to_csv(save_path)
