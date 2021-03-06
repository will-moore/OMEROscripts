#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
 components/tools/OmeroPy/scripts/omero/util_scripts/Dataset_To_Well.py

-----------------------------------------------------------------------------
  Copyright (C) 2006-2014 University of Dundee. All rights reserved.


  This program is free software; you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation; either version 2 of the License, or
  (at your option) any later version.
  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
  GNU General Public License for more details.

  You should have received a copy of the GNU General Public License along
  with this program; if not, write to the Free Software Foundation, Inc.,
  51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

------------------------------------------------------------------------------

This script converts a Dataset of Images to Fields in a Well.

@author Damir Sudar (based on Will Moore's Dataset_To_Plate.py)
<a href="mailto:dsudar@qimagingsys.com">dsudar@qimagingsys.com</a>
@version 0.0.1
"""

import omero.scripts as scripts
from omero.gateway import BlitzGateway
import omero.util.script_utils as script_utils
import omero

from omero.rtypes import rint, rlong, rstring, robject, unwrap


def addImageToWell(conn, image, well, removeFrom=None):
    """
    Add the Image to a Plate, creating a new well at the specified column and
    row
    NB - This will fail if there is already a well at that point
    """
    updateService = conn.getUpdateService()

    try:
        ws = omero.model.WellSampleI()
        ws.image = omero.model.ImageI(image.id, False)
        ws.well = well
        well.addWellSample(ws)
        updateService.saveObject(ws)
    except:
        print "Failed to add image to well sample"
        return False

    # remove from Datast
    if removeFrom is not None:
        links = list(image.getParentLinks(removeFrom.id))
        print "     Removing image from Dataset: %s" \
            % removeFrom.id
        for l in links:
            conn.deleteObjectDirect(l._obj)
    return True


def dataset_to_well(conn, scriptParams, datasetId, plateId):

    dataset = conn.getObject("Dataset", datasetId)
    if dataset is None:
        print "No dataset found for ID %s" % datasetId
        return

    updateService = conn.getUpdateService()

    row = scriptParams["Well_Column"]
    col = scriptParams["Well_Row"]

    plate = conn.getObject("Plate", plateId)
    if plate is None:
        print "No Plate found for ID %s" % plateId
        return
        
    print "Moving images from Dataset: %d to Plate: %d; Row: %d, Column: %d" \
        % (dataset.id, plate.getId(), row, col)

    # sort images by name
    images = list(dataset.listChildren())
    datasetImgCount = len(images)
    if "Filter_Names" in scriptParams:
        filterBy = scriptParams["Filter_Names"]
        print "Filtering images for names containing: %s" % filterBy
        images = [i for i in images if (i.getName().find(filterBy) >= 0)]
    images.sort(key=lambda x: x.name.lower())

    # Do we try to remove images from Dataset and Delete Dataset when/if empty?
    removeFrom = None
    removeDataset = "Remove_From_Dataset" in scriptParams and \
        scriptParams["Remove_From_Dataset"]
    if removeDataset:
        removeFrom = dataset

    well = omero.model.WellI()
    well.plate = omero.model.PlateI(plate.getId(), False)
    well.column = rint(col-1)
    well.row = rint(row-1)
    well = updateService.saveAndReturnObject(well)

    for image in images:
        addedCount = addImageToWell(conn, image, well, removeFrom)

    # if user wanted to delete dataset, AND it's empty we can delete dataset
    deleteDataset = False   # Turning this functionality off for now.
    deleteHandle = None
    if deleteDataset:
        if datasetImgCount == addedCount:
            dcs = list()
            print 'Deleting Dataset %d %s' % (dataset.id, dataset.name)
            options = None  # {'/Image': 'KEEP'}    # don't delete the images!
            dcs.append(omero.api.delete.DeleteCommand(
                "/Dataset", dataset.id, options))
            deleteHandle = conn.getDeleteService().queueDelete(dcs)
    return deleteHandle


def dataset_to_platewell(conn, scriptParams):

    updateService = conn.getUpdateService()

    message = ""

    # Get the datasets ID
    datasets, logMessage = script_utils.getObjects(conn, scriptParams)
    message += logMessage

    def has_images_linked_to_well(dataset):
        params = omero.sys.ParametersI()
        query = "select count(well) from Well as well "\
                "left outer join well.wellSamples as ws " \
                "left outer join ws.image as img "\
                "where img.id in (:ids)"
        params.addIds([i.getId() for i in dataset.listChildren()])
        n_wells = unwrap(conn.getQueryService().projection(
            query, params, conn.SERVICE_OPTS)[0])[0]
        if n_wells > 0:
            print "Dataset %s contains images linked to wells." \
                % dataset.getId()
            return True
        else:
            return False

    # Exclude datasets containing images already linked to a well
    nDatasets = len(datasets)
    datasets = [x for x in datasets if not has_images_linked_to_well(x)]
    if len(datasets) < nDatasets:
        message += "Excluded %s out of %s dataset(s). " \
            % (nDatasets - len(datasets), nDatasets)

    # Return if all input dataset are not found or excluded
    if not datasets:
        return message

    # Filter dataset IDs by permissions
    IDs = [ds.getId() for ds in datasets if ds.canLink()]
    if len(IDs) != len(datasets):
        permIDs = [str(ds.getId()) for ds in datasets if not ds.canLink()]
        message += "You do not have the permissions to add the images from"\
            " the dataset(s): %s." % ",".join(permIDs)
    if not IDs:
        return message

    plate = None
    newplate = None
    if "Plate" in scriptParams and len(scriptParams["Plate"]) > 0:
        sp = scriptParams["Plate"]
        # see if this is an ID of existing plate
        try:
            plateId = long(sp)
            plate = conn.getObject("Plate", plateId)
        except ValueError:
            pass
        # if not, create one
        if plate is None:
            newplate = omero.model.PlateI()
            newplate.name = rstring(sp)
            newplate.columnNamingConvention = rstring(str(scriptParams["Column_Names"]))
            # 'letter' or 'number'
            newplate.rowNamingConvention = rstring(str(scriptParams["Row_Names"]))
            newplate = updateService.saveAndReturnObject(newplate)
            plate = conn.getObject("Plate", newplate.id.val)
        
    deletes = []
    for datasetId in IDs:
        deleteHandle = dataset_to_well(conn, scriptParams,
                                        datasetId, plate.id)
        if deleteHandle is not None:
            deletes.append(deleteHandle)

    # wait for any deletes to finish
    for handle in deletes:
        cb = omero.callbacks.DeleteCallbackI(conn.c, handle)
        while True:  # ms
            if cb.block(100) is None:
                print "Waiting for delete"
            else:
                break
        err = handle.errors()
        if err > 0:
            print "Delete error", err
        else:
            print "Delete OK"

    if newplate:
        message += "New plate created: %s." % newplate.getName().val
        robj = newplate
    else:
         robj = None
         
    return robj, message


def runAsScript():
    """
    The main entry point of the script, as called by the client via the
    scripting service, passing the required parameters.
    """

    dataTypes = [rstring('Dataset')]
    rowColNaming = [rstring('letter'), rstring('number')]

    client = scripts.client(
        'Dataset_To_Well.py',
        """Take a Dataset of Images and put them in a Well, \
of an existing or new Plate.
See http://help.openmicroscopy.org/scripts.html""",

        scripts.String(
            "Data_Type", optional=False, grouping="1",
            description="Choose source of images (only Dataset supported)",
            values=dataTypes, default="Dataset"),

        scripts.List(
            "IDs", optional=False, grouping="2",
            description="Dataset ID to convert to new"
            " Well.").ofType(rlong(0)),

        scripts.String(
            "Filter_Names", grouping="2.1",
            description="Filter the images by names that contain this value"),
            
        scripts.String(
            "Column_Names", grouping="3", default='number',
            values=rowColNaming,
            description="""Name plate columns with 'number' or 'letter'",
            " (please specify for new Plate!!!)"""),

        scripts.String(
            "Row_Names", grouping="4", default='letter',
            values=rowColNaming,
            description="""Name plate rows with 'number' or 'letter'",
            " (please specify for new Plate!!!)"""),

        scripts.String(
            "Plate", grouping="5", optional=False,
            description="Destination Plate. Enter Name of new"
            " plate or ID of existing plate"),

	    scripts.Int(
		    "Well_Row", grouping="5.1", default=1,
            description="Put Images as Fields into specified Well Row", min=1),

	    scripts.Int(
            "Well_Column", grouping="5.2", default=1,
            description="Put Images as Fields into specified Well Column", min=1),

		scripts.Bool(
            "Remove_From_Dataset", grouping="6", default=True,
            description="Remove Images from Dataset as they are added to"
            " Plate"),

        version="0.0.1",
        authors=["Damir Sudar"],
        institutions=["Quantitative Imaging Systems LLC"],
        contact="ome-users@lists.openmicroscopy.org.uk",
    )

    try:
        scriptParams = client.getInputs(unwrap=True)
        print scriptParams

        # wrap client to use the Blitz Gateway
        conn = BlitzGateway(client_obj=client)

        # convert Dataset(s) to Plate. Returns new plate if created
        newObj, message = dataset_to_platewell(conn, scriptParams)

        client.setOutput("Message", rstring(message))
        if newObj:
            client.setOutput("New_Object", robject(newObj))
            
    finally:
        client.closeSession()

if __name__ == "__main__":
    runAsScript()
