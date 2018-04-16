import pygplates
import numpy as np
import healpy as hp
import pandas as pd
import matplotlib.pyplot as plt

from proximity_query import find_closest_geometries_to_points
import ptt.subduction_convergence as sc


class ReconstructionModel(object):
    
    def __init__(self, name):
        self.name = name
        self.rotation_model = []    # creates a new empty list for each dog
        self.static_polygons = []
        self.dynamic_polygons = []

    def add_rotation_model(self, rotation_file):
        self.rotation_model.append(rotation_file)
        
    def add_static_polygons(self, static_polygons_file):
        self.static_polygons.append(static_polygons_file)
        
    def add_dynamic_polygons(self, dynamic_polygons_file):
        self.dynamic_polygons.append(dynamic_polygons_file)
        
    def plate_snapshot(self, reconstruction_time, anchor_plate_id=0):
        resolved_topologies = []
        resolved_topological_sections = []
        pygplates.resolve_topologies(self.dynamic_polygons, 
                                     self.rotation_model,
                                     resolved_topologies, 
                                     reconstruction_time,
                                     resolved_topological_sections,
                                     anchor_plate_id=anchor_plate_id)
        
        return PlateSnapshot(resolved_topologies,
                             resolved_topological_sections,
                             self.rotation_model,
                             reconstruction_time,
                             anchor_plate_id)
    
    def subduction_convergence(self, reconstruction_time, 
                              velocity_delta_time=1.,
                              threshold_sampling_distance_radians=2,
                              anchor_plate_id=0):

        result = sc.subduction_convergence(
            self.rotation_model,
            self.dynamic_polygons,
            threshold_sampling_distance_radians,
            reconstruction_time,
            velocity_delta_time,
            anchor_plate_id)
        
        # Make a flat list of subduction stats to input into the proximity test
        subduction_data = []
        for data in result:
            subduction_data.append(data+(reconstruction_time,))
    
        # Data frame template defining the column names
        DataFrameTemplate = ('lon','lat','conv_rate','conv_obliq','migr_rate',
                             'migr_obliq','arc_length','arc_azimuth',
                             'subducting_plate','overriding_plate','time')
        
        # convert list array to dataframe
        df = pd.DataFrame(subduction_data, columns = DataFrameTemplate)
        
        return SubductionConvergence(df)
        

class PlateSnapshot(object):

    def __init__(self, 
                 resolved_topologies,
                 resolved_topological_sections,
                 rotation_model,
                 reconstruction_time,
                 anchor_plate_id):
        
        self.reconstruction_time = reconstruction_time
        self.anchor_plate = anchor_plate_id
        self.rotation_model = rotation_model
        self.resolved_topologies = resolved_topologies
        self.plate_count = len(resolved_topologies)
        self.plate_ids = [resolved_topology.get_resolved_feature().get_reconstruction_plate_id() \
                          for resolved_topology in resolved_topologies]
        self.plate_areas = [resolved_topology.get_resolved_geometry().get_area() * pygplates.Earth.mean_radius_in_kms\
                            for resolved_topology in resolved_topologies]
        self.plate_perimeters = [resolved_topology.get_resolved_geometry().get_arc_length() * pygplates.Earth.mean_radius_in_kms\
                            for resolved_topology in resolved_topologies] 
        self.plate_centroids = [resolved_topology.get_resolved_geometry().get_interior_centroid()\
                                for resolved_topology in resolved_topologies]

    def velocity_field(self, velocity_domain_features=None, velocity_type='both', delta_time=1.):

        if velocity_domain_features is None:
            velocity_domain_features = point_distribution_on_sphere(distribution_type='healpix',N=32).meshnode_feature

        # All domain points and associated (magnitude, azimuth, inclination) velocities for the current time.
        all_domain_points = []
        all_velocities_en = []
        all_velocities_magaz = []
        plate_ids = []

        rotation_model = pygplates.RotationModel(self.rotation_model)

        # Partition our velocity domain features into our topological plate polygons at the current 'time'.
        plate_partitioner = pygplates.PlatePartitioner(self.resolved_topologies, 
                                                       rotation_model)

        for velocity_domain_feature in velocity_domain_features:

            # A velocity domain feature usually has a single geometry but we'll assume it can be any number.
            # Iterate over them all.
            for velocity_domain_geometry in velocity_domain_feature.get_geometries():

                for velocity_domain_point in velocity_domain_geometry.get_points():

                    all_domain_points.append(velocity_domain_point)

                    partitioning_plate = plate_partitioner.partition_point(velocity_domain_point)
                    if partitioning_plate:

                        # We need the newly assigned plate ID to get the equivalent stage rotation of that tectonic plate.
                        partitioning_plate_id = partitioning_plate.get_feature().get_reconstruction_plate_id()

                        # Get the stage rotation of partitioning plate from 'time + delta_time' to 'time'.
                        equivalent_stage_rotation = rotation_model.get_rotation(self.reconstruction_time, 
                                                                                partitioning_plate_id, 
                                                                                self.reconstruction_time + delta_time)

                        # Calculate velocity at the velocity domain point.
                        # This is from 'time + delta_time' to 'time' on the partitioning plate.
                        velocity_vectors = pygplates.calculate_velocities(
                            [velocity_domain_point],
                            equivalent_stage_rotation,
                            delta_time)

                        # Convert global 3D velocity vectors to local (magnitude, azimuth, inclination) tuples (one tuple per point).
                        velocities = pygplates.LocalCartesian.convert_from_geocentric_to_north_east_down(
                                [velocity_domain_point],
                                velocity_vectors)
                        all_velocities_en.append(velocities[0])
                        
                        # Convert global 3D velocity vectors to local (magnitude, azimuth, inclination) tuples (one tuple per point).
                        velocities = pygplates.LocalCartesian.convert_from_geocentric_to_magnitude_azimuth_inclination(
                                [velocity_domain_point],
                                velocity_vectors)
                        all_velocities_magaz.append(velocities[0])

                        plate_ids.append(partitioning_plate_id)

                    else:
                        # If point is not within a polygon, set velocity and plate_id to zero
                        all_velocities_en.append((0,0,0))
                        all_velocities_magaz.append((0,0,0))
                        plate_ids.append(0)

        vel_east=[]
        vel_north=[]
        vel_mag = []
        vel_azim = []
        for velocity_vector in all_velocities_en:
            if getattr(velocity_vector,'get_x',None) is not None:
                vel_east.append(velocity_vector.get_y())
                vel_north.append(velocity_vector.get_x())
            else:
                vel_east.append(0.)
                vel_north.append(0.)

        for velocity_vector in all_velocities_magaz:
            vel_mag.append(velocity_vector[0])
            vel_azim.append(velocity_vector[1])

        pt_lon = []
        pt_lat = []
        for pt in all_domain_points:
            pt_lon.append(pt.to_lat_lon()[1])
            pt_lat.append(pt.to_lat_lon()[0])

        return VelocityField(pt_lat,pt_lon,vel_east,vel_north,vel_mag,vel_azim,plate_ids)


class VelocityField(object):
    
    def __init__(self, pt_lat,pt_lon,vel_east,vel_north,vel_mag,vel_azim,plate_ids):
        self.longitude = pt_lon
        self.latitude = pt_lat
        self.plate_id = plate_ids
        self.velocity_east = vel_east
        self.velocity_north = vel_north
        self.velocity_magnitude = vel_mag
        self.velocity_azimuth = vel_azim

    def rms_velocity(self, plate_id_selection=None):

        if plate_id_selection is None:
            return np.sqrt(np.mean(np.square(np.asarray(self.velocity_magnitude))))

        elif type(plate_id_selection) is list:
            index = [plate_id in plate_id_selection for plate_id in self.plate_id]

        else:
            index = [plate_id == plate_id_selection for plate_id in self.plate_id]
        
        #print index
        return np.sqrt(np.mean(np.square(np.asarray(self.velocity_magnitude)[index])))



class SubductionConvergence(object):
    
    def __init__(self, df):
        self.df = df
    

    def plot(self):
        plt.figure()
        plt.scatter(self.df.lon, self.df.lat,c=self.df.conv_rate,edgecolors='')
        plt.show()



class age_coded_point_dataset(object):
  
    def __init__(self, df, longitude_field, latitude_field, age_field):
        self.df = df
        self.point_features = []
        for index,row in df.iterrows():
            point = pygplates.PointOnSphere(float(row[latitude_field]),float(row[longitude_field]))
            point_feature = pygplates.Feature()
            point_feature.set_geometry(point)
            point_feature.set_valid_time(row[age_field],-999.)
            self.point_features.append(point_feature)


    def assign_reconstruction_model(self,reconstruction_model):
        partitioned_point_features = pygplates.partition_into_plates(reconstruction_model.static_polygons,
                                                                     reconstruction_model.rotation_model,
                                                                     self.point_features)
        self.point_features = partitioned_point_features
        self.reconstruction_model = reconstruction_model
    
    
    def reconstruct(self,reconstruction_time,anchor_plate_id=0):
        reconstructed_features = []
        pygplates.reconstruct(self.point_features,
                              self.reconstuction_model.rotation_model,
                              reconstructed_features,
                              reconstruction_time,
                              anchor_plate_id=anchor_plate_id)
        
        return reconstructed_features
    
    
    def plot_reconstructed(self,reconstruction_time,anchor_plate_id=0):
        reconstructed_features = []
        pygplates.reconstruct(self.point_features,
                              self.reconstruction_model.rotation_model,
                              reconstructed_features,
                              reconstruction_time,
                              anchor_plate_id=anchor_plate_id)
        
        plt.figure()
        for reconstructed_feature in reconstructed_features:
            plt.plot(reconstructed_feature.get_reconstructed_geometry().to_lat_lon()[1],
                     reconstructed_feature.get_reconstructed_geometry().to_lat_lon()[0],'ro')
            plt.axis([-180,180,-90,90])
        plt.title('%0.2f Ma' % reconstruction_time)
        plt.show()
        
        
    def reconstruct_to_time_of_appearance(self,ReconstructTime='BirthTime',anchor_plate_id=0):
        
        rotation_model = pygplates.RotationModel(self.reconstruction_model.rotation_model)
        recon_points = []
        for point_feature in self.point_features:
            if ReconstructTime is 'MidTime':
                time = (point_feature.get_valid_time()[0]+point_feature.get_valid_time()[1])/2.
            else:
                time = point_feature.get_valid_time()[0]
            if point_feature.get_reconstruction_plate_id()!=0:
                point_rotation = rotation_model.get_rotation(time,
                                                             point_feature.get_reconstruction_plate_id(),
                                                             anchor_plate_id=anchor_plate_id)
                reconstructed_point = point_rotation * point_feature.get_geometry()
                recon_points.append([reconstructed_point.to_lat_lon()[1],
                                     reconstructed_point.to_lat_lon()[0],
                                     time])
            
        return recon_points


class point_distribution_on_sphere(object):

    def __init__(self, distribution_type='random', N=10000):
        
        if distribution_type=='healpix':               
            othetas,ophis = hp.pix2ang(N,np.arange(12*N**2))
            othetas = np.pi/2-othetas
            ophis[ophis>np.pi] -= np.pi*2

            # ophis -> longitude, othetas -> latitude
            self.longitude = np.degrees(ophis)
            self.latitude = np.degrees(othetas)

        elif distribution_type=='random':
            # function to call Marsaglia's method and return Long/
            # Lat arrays

            ## Marsaglia's method
            dim = 3
            norm = np.random.normal
            normal_deviates = norm(size=(dim, N))

            radius = np.sqrt((normal_deviates**2).sum(axis=0))
            points = normal_deviates/radius

            Long=[], Lat=[]
            for xyz in points.T:
                LL = pygplates.PointOnSphere((xyz))
                Lat.append(LL.to_lat_lon()[0])
                Long.append(LL.to_lat_lon()[1])

            self.longitude = np.array(Long)
            self.latitude = np.array(Lat)

        self.multipoint = pygplates.MultiPointOnSphere(zip(self.latitude,self.longitude))
        self.meshnode_feature = pygplates.Feature(pygplates.FeatureType.create_from_qualified_string('gpml:MeshNode'))
        self.meshnode_feature.set_geometry(self.multipoint)

    def to_gpml(self, filename):

        pygplates.FeatureCollection(self.meshnode_feature).write(filename)

    def point_feature_heatmap(self, target_features):

        res = find_closest_geometries_to_points(target_features,
                                                [self.multipoint],
                                                return_closest_position = True,
                                                return_closest_index = True)

        bin_indices = zip(*res)[2]
        bin_counts = []
        for j in range(len(self.multipoint.get_points())):
            bin_counts.append(bin_indices.count(j))

        return bin_counts

